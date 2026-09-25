"""Database connection and query layer."""
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
from asyncpg import Pool
from collections.abc import AsyncGenerator

class Database:
    """Manages PostgreSQL connection pool and provides query methods."""

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 10):
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self._pool: Optional[Pool] = None

    async def connect(self) -> None:
        """Create the connection pool."""
        self._pool = await asyncpg.create_pool(
            self.dsn,
            min_size=self.min_size,
            max_size=self.max_size,
            server_settings={"statement_timeout": "5000"},
            command_timeout=6,
        )

    async def disconnect(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[dict, None]:
        """Acquire a connection from the pool."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")
        async with self._pool.acquire() as conn:
            yield conn

    # ---- Query helpers ----

    async def acquire_fetch(self, query: str, *args):
        """Fetch multiple rows using a pooled connection."""
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)

    async def acquire_fetchrow(self, query: str, *args):
        """Fetch a single row using a pooled connection."""
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args)

    # ---- Resource queries ----

    async def get_team_topology(self, team_id: str) -> Optional[dict]:
        """Fetch team topology using the closure table for efficient traversal."""
        query = """
        WITH team_members AS (
            SELECT dis_id, display_name, email, manager_id, team_id,
                   timezone, seat_building, seat_floor, hired_at
            FROM employees
            WHERE team_id = $1
        )
        SELECT
            tm.*,
            (SELECT json_agg(json_build_object(
                'ancestor_id', rc.ancestor_id,
                'descendant_id', rc.descendant_id,
                'depth', rc.depth
            ))
            FROM reporting_closure rc
            WHERE rc.ancestor_id = tm.dis_id AND rc.depth > 0
            ) AS reports
        FROM team_members tm
        """
        rows = await self.acquire_fetch(query, team_id)

        if not rows:
            return None

        members = [dict(r) for r in rows]
        return {
            "team_id": team_id,
            "team_name": team_id.replace("_", " ").title(),
            "members": members,
            "cross_team_edges": [],  # Extend with a separate query for dotted-lines
        }

    async def get_employee_presence(self, dis_id: str) -> Optional[dict]:
        """Fetch employee presence with computed local time."""
        query = """
        SELECT
            e.dis_id, e.display_name, e.timezone,
            p.status, p.last_seen_at,
            -- Compute local time server-side so the LLM never does TZ math
            (NOW() AT TIME ZONE e.timezone) AS local_time
        FROM employees e
        LEFT JOIN presence p ON p.dis_id = e.dis_id
        WHERE e.dis_id = $1
        """
        row = await self.acquire_fetchrow(query, dis_id)

        if not row:
            return None

        return {
            "dis_id": row["dis_id"],
            "display_name": row["display_name"],
            "status": row["status"] or "offline",
            "local_time": row["local_time"].isoformat(),
            "timezone": row["timezone"],
            "working_hours": "09:00-17:30",  # Could be a column
            "next_meeting": None,  # Extend from calendar integration
        }

    # ---- Tool queries ----

    async def find_peers_by_skill(
        self,
        skill: str,
        timezone: str,
        exclude_team_id: Optional[str] = None,
        max_results: int = 5,
    ) -> list[dict]:
        """
        Find employees with a given skill, ranked by timezone overlap
        and cross-team diversity.

        Uses a covering index on (skill, dis_id) for fast lookup.
        """
        query = """
        WITH skill_peers AS (
            SELECT
                e.dis_id, e.display_name, e.team_id, e.timezone,
                s.level AS skill_level
            FROM employees e
            JOIN skills s ON s.dis_id = e.dis_id
            WHERE s.skill = $1
              AND ($2::text IS NULL OR e.team_id != $2)
        ),
        scored AS (
            SELECT
                sp.*,
                -- Approximate timezone overlap in hours (working 9-17).
                -- Real overlap would need per-tz working-hour tables; this
                -- is a first-pass heuristic and is documented as such.
                CASE
                    WHEN sp.timezone = $3 THEN 8.0
                    ELSE 4.0
                END AS timezone_overlap_hours,
                CASE WHEN sp.team_id != $2 THEN 1 ELSE 0 END AS cross_team_bonus,
                (sp.skill_level::float / 5.0) AS skill_weight
            FROM skill_peers sp
        )
        SELECT
            dis_id, display_name, team_id, timezone,
            skill_level,
            timezone_overlap_hours,
            (cross_team_bonus = 1) AS cross_team,
            -- Composite score: skill 50%, timezone 30%, cross-team 20%
            (skill_weight * 0.5 + (timezone_overlap_hours / 8.0) * 0.3 + cross_team_bonus * 0.2) AS score,
            -- Compute local time for the candidate
            (NOW() AT TIME ZONE timezone) AS local_time
        FROM scored
        ORDER BY score DESC
        LIMIT $4
        """
        rows = await self.acquire_fetch(
            query, skill, exclude_team_id, timezone, max_results
        )

        results = []
        for row in rows:
            results.append({
                "dis_id": row["dis_id"],
                "display_name": row["display_name"],
                "team_id": row["team_id"],
                "timezone": row["timezone"],
                "local_time": row["local_time"].isoformat(),
                "skill_level": row["skill_level"],
                "timezone_overlap_hours": row["timezone_overlap_hours"],
                "cross_team": row["cross_team"],
                "reason": (
                    f"Skill level {row['skill_level']}/5; "
                    f"{row['timezone_overlap_hours']}h overlap; "
                    f"{'cross-team' if row['cross_team'] else 'same team'}"
                ),
            })

        return results

    async def get_onboarding_candidates(
        self,
        new_hire_id: str,
        min_tenure_months: int = 6,
    ) -> list[dict]:
        """
        Find buddy candidates for a new hire.
        Excludes the new hire's direct manager and anyone with insufficient tenure.
        """
        query = """
        WITH new_hire AS (
            SELECT dis_id, team_id, manager_id, seat_building, seat_floor, timezone
            FROM employees
            WHERE dis_id = $1
        ),
        candidates AS (
            SELECT
                e.dis_id, e.display_name, e.team_id, e.seat_building, e.seat_floor,
                e.timezone, e.hired_at,
                EXTRACT(YEAR FROM AGE(NOW(), e.hired_at)) * 12 +
                EXTRACT(MONTH FROM AGE(NOW(), e.hired_at)) AS tenure_months
            FROM employees e, new_hire nh
            WHERE e.dis_id != nh.dis_id
              AND e.manager_id IS DISTINCT FROM nh.manager_id
              AND e.dis_id != nh.manager_id
        )
        SELECT * FROM candidates
        WHERE tenure_months >= $2
        ORDER BY
            -- Prefer same building/floor, then same team, then timezone match
            CASE WHEN seat_building = (SELECT seat_building FROM new_hire) THEN 1 ELSE 0 END DESC,
            CASE WHEN seat_floor = (SELECT seat_floor FROM new_hire) THEN 1 ELSE 0 END DESC,
            CASE WHEN team_id = (SELECT team_id FROM new_hire) THEN 1 ELSE 0 END DESC,
            tenure_months DESC
        """
        rows = await self.acquire_fetch(query, new_hire_id, min_tenure_months)
        return [dict(r) for r in rows]