"""REST shim over the MCP tool/resource handlers.

The React frontend talks to these routes. They invoke the same Database
methods the MCP handlers use, so there is exactly one source of truth.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from enterprise_graph_mcp.db import Database


def build_router(db: Database) -> APIRouter:
    """Return a FastAPI router bound to the given Database instance."""
    router = APIRouter(prefix="/api")

    # ---- Listings ----

    @router.get("/teams")
    async def list_teams():
        async with db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT team_id FROM employees ORDER BY team_id"
            )
        return [{"team_id": r["team_id"]} for r in rows]

    @router.get("/employees")
    async def list_employees(team_id: str | None = None):
        if team_id:
            rows = await db.acquire_fetch(
                "SELECT dis_id, display_name, team_id, timezone "
                "FROM employees WHERE team_id = $1 ORDER BY dis_id",
                team_id,
            )
        else:
            rows = await db.acquire_fetch(
                "SELECT dis_id, display_name, team_id, timezone "
                "FROM employees ORDER BY dis_id"
            )
        return [dict(r) for r in rows]

    # ---- Resources ----

    @router.get("/teams/{team_id}/topology")
    async def get_topology(team_id: str):
        result = await db.get_team_topology(team_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Team not found: {team_id}")
        return result

    @router.get("/employees/{dis_id}/presence")
    async def get_presence(dis_id: str):
        result = await db.get_employee_presence(dis_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Employee not found: {dis_id}")
        return result

    # ---- Tools ----

    class PeerQuery(BaseModel):
        skill: str
        timezone: str
        exclude_team_id: str | None = None
        max_results: int = 5

    @router.post("/tools/find-cross-functional-peers")
    async def find_peers(q: PeerQuery):
        return await db.find_peers_by_skill(
            skill=q.skill,
            timezone=q.timezone,
            exclude_team_id=q.exclude_team_id,
            max_results=q.max_results,
        )

    class BuddyQuery(BaseModel):
        new_hire_id: str
        cohort_size: int = 1

    @router.post("/tools/onboarding-buddy-pairings")
    async def buddy_pairings(q: BuddyQuery):
        from enterprise_graph_mcp.ranking import generate_buddy_pairings

        candidates = await db.get_onboarding_candidates(q.new_hire_id)
        if not candidates:
            return []

        new_hire = await db.acquire_fetchrow(
            "SELECT * FROM employees WHERE dis_id = $1", q.new_hire_id
        )
        if not new_hire:
            raise HTTPException(status_code=404, detail=f"New hire not found: {q.new_hire_id}")

        new_hire_skills = await db.acquire_fetch(
            "SELECT skill FROM skills WHERE dis_id = $1", q.new_hire_id
        )

        return generate_buddy_pairings(
            new_hire=dict(new_hire),
            new_hire_skills=[s["skill"] for s in new_hire_skills],
            candidates=candidates,
            cohort_size=q.cohort_size,
        )

    return router