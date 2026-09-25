 # Background sync workers
"""Background sync workers.

Pulls employee, presence, and skill data from external systems into the
local PostgreSQL database. Designed to be:

  - **Idempotent**: every write uses ON CONFLICT with a source_updated_at
    watermark, so replays and retries are safe and stale data never wins.
  - **Pluggable**: each source is a Protocol. Implement a real adapter
    (Slack, Workday, Jira) or a fake for tests.
  - **Bounded**: each sync runs under its own timeout. A slow source
    cannot block the others.
  - **Observable**: every run emits a span and a structured log line.

The orchestrator (`SyncOrchestrator`) is what `server.py` schedules as a
background task. Individual providers are stateless and easy to test.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from opentelemetry import trace

from enterprise_graph_mcp.db import Database

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


# =============================================================================
# DATA TRANSFER OBJECTS
# =============================================================================
#
# DTOs describe the shape providers must return. Keeping them separate from
# the Pydantic API models means an external provider can't accidentally
# change what the REST layer exposes.

@dataclass(frozen=True)
class EmployeeRecord:
    """A single employee, as seen by an HRIS provider."""
    dis_id: str
    display_name: str
    email: str
    manager_id: str | None
    team_id: str
    timezone: str
    seat_building: str | None = None
    seat_floor: int | None = None
    hired_at: datetime | None = None
    source_updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class SkillRecord:
    """A skill held by an employee."""
    dis_id: str
    skill: str
    level: int  # 1..5
    source_updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class PresenceRecord:
    """A snapshot of an employee's chat/calendar presence."""
    dis_id: str
    status: str  # 'active' | 'away' | 'dnd' | 'offline'
    source: str  # 'slack' | 'webex' | 'calendar'
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source_updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# PROVIDER PROTOCOLS
# =============================================================================
#
# Protocols (not ABCs) so providers don't need to inherit from anything.
# A class with the right methods satisfies the protocol structurally.

@runtime_checkable
class HrisProvider(Protocol):
    """Source of truth for org structure, seats, and skills."""

    async def fetch_employees(
        self, since: datetime | None
    ) -> list[EmployeeRecord]:
        """Return employees updated since `since` (or all if None)."""
        ...

    async def fetch_skills(
        self, since: datetime | None
    ) -> list[SkillRecord]:
        """Return skill assignments updated since `since`."""
        ...


@runtime_checkable
class PresenceProvider(Protocol):
    """Source of truth for live chat/calendar presence."""

    async def fetch_presence(
        self, since: datetime | None
    ) -> list[PresenceRecord]:
        """Return presence snapshots updated since `since`."""
        ...


# =============================================================================
# SYNC RESULTS
# =============================================================================

@dataclass
class SyncResult:
    """Summary of a single provider's sync run."""
    provider: str
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    error: str | None = None
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None

    def __str__(self) -> str:
        if not self.ok:
            return f"{self.provider}: FAILED ({self.error}) in {self.duration_seconds:.2f}s"
        return (
            f"{self.provider}: +{self.inserted} ~{self.updated} "
            f"={self.skipped} in {self.duration_seconds:.2f}s"
        )


# =============================================================================
# SYNC STATE
# =============================================================================
#
# Track the last successful run per provider so subsequent runs can pull
# only the delta. In production this belongs in a `sync_state` table, but
# for a single-process server an in-memory dict is sufficient and simpler.

@dataclass
class _SyncState:
    last_employees_sync: datetime | None = None
    last_skills_sync: datetime | None = None
    last_presence_sync: datetime | None = None


# =============================================================================
# ORCHESTRATOR
# =============================================================================

class SyncOrchestrator:
    """Coordinates periodic syncs from all configured providers.

    Usage:

        orchestrator = SyncOrchestrator(
            db=db,
            hris=WorkdayProvider(...),
            presence=SlackProvider(...),
            interval_seconds=300,
        )
        asyncio.create_task(orchestrator.run_forever())

    The orchestrator is safe to start multiple times but you shouldn't —
    each instance owns its own loop and its own state.
    """

    def __init__(
        self,
        db: Database,
        hris: HrisProvider | None = None,
        presence: PresenceProvider | None = None,
        interval_seconds: int = 300,
        per_provider_timeout: float = 30.0,
    ) -> None:
        self.db = db
        self.hris = hris
        self.presence = presence
        self.interval_seconds = interval_seconds
        self.per_provider_timeout = per_provider_timeout
        self._state = _SyncState()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        """Signal the run loop to exit after the current cycle."""
        self._stop.set()

    async def run_forever(self) -> None:
        """Run syncs on a fixed interval until `stop()` is called."""
        logger.info(
            "sync orchestrator starting (interval=%ds, hris=%s, presence=%s)",
            self.interval_seconds,
            "yes" if self.hris else "no",
            "yes" if self.presence else "no",
        )
        try:
            while not self._stop.is_set():
                try:
                    await self.run_once()
                except Exception:
                    logger.exception("sync cycle failed; will retry next interval")

                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.interval_seconds
                    )
                except asyncio.TimeoutError:
                    pass  # normal — time to run again
        finally:
            logger.info("sync orchestrator stopped")

    async def run_once(self) -> list[SyncResult]:
        """Run every configured provider once. Returns per-provider results.

        Failures in one provider are isolated: the others still run.
        """
        results: list[SyncResult] = []

        if self.hris is not None:
            results.append(await self._sync_hris())
        if self.presence is not None:
            results.append(await self._sync_presence())

        for r in results:
            log = logger.info if r.ok else logger.error
            log("sync result: %s", r)

        return results

    # ---- HRIS sync ----

    async def _sync_hris(self) -> SyncResult:
        assert self.hris is not None
        result = SyncResult(provider="hris")
        start = asyncio.get_event_loop().time()

        with tracer.start_as_current_span("sync.hris") as span:
            try:
                employees = await asyncio.wait_for(
                    self.hris.fetch_employees(self._state.last_employees_sync),
                    timeout=self.per_provider_timeout,
                )
                emp_result = await self._upsert_employees(employees)
                result.inserted += emp_result["inserted"]
                result.updated += emp_result["updated"]
                result.skipped += emp_result["skipped"]

                skills = await asyncio.wait_for(
                    self.hris.fetch_skills(self._state.last_skills_sync),
                    timeout=self.per_provider_timeout,
                )
                skill_result = await self._upsert_skills(skills)
                result.inserted += skill_result["inserted"]
                result.updated += skill_result["updated"]
                result.skipped += skill_result["skipped"]

                now = datetime.now(timezone.utc)
                self._state.last_employees_sync = now
                self._state.last_skills_sync = now

                span.set_attribute("sync.employees.count", len(employees))
                span.set_attribute("sync.skills.count", len(skills))

            except asyncio.TimeoutError:
                result.error = f"timeout after {self.per_provider_timeout}s"
                span.set_attribute("sync.error", "timeout")
            except Exception as exc:
                result.error = str(exc)
                span.record_exception(exc)

        result.duration_seconds = asyncio.get_event_loop().time() - start
        return result

    # ---- presence sync ----

    async def _sync_presence(self) -> SyncResult:
        assert self.presence is not None
        result = SyncResult(provider="presence")
        start = asyncio.get_event_loop().time()

        with tracer.start_as_current_span("sync.presence") as span:
            try:
                records = await asyncio.wait_for(
                    self.presence.fetch_presence(self._state.last_presence_sync),
                    timeout=self.per_provider_timeout,
                )
                upsert = await self._upsert_presence(records)
                result.inserted += upsert["inserted"]
                result.updated += upsert["updated"]
                result.skipped += upsert["skipped"]

                self._state.last_presence_sync = datetime.now(timezone.utc)
                span.set_attribute("sync.presence.count", len(records))

            except asyncio.TimeoutError:
                result.error = f"timeout after {self.per_provider_timeout}s"
                span.set_attribute("sync.error", "timeout")
            except Exception as exc:
                result.error = str(exc)
                span.record_exception(exc)

        result.duration_seconds = asyncio.get_event_loop().time() - start
        return result

    # ---- upserts ----
    #
    # Each upsert uses `WHERE excluded.source_updated_at > table.source_updated_at`
    # so a stale payload (out-of-order delivery, retry of an old batch) is
    # silently dropped instead of clobbering fresher data.

    async def _upsert_employees(self, records: list[EmployeeRecord]) -> dict:
        if not records:
            return {"inserted": 0, "updated": 0, "skipped": 0}

        query = """
        INSERT INTO employees (
            dis_id, display_name, email, manager_id, team_id,
            timezone, seat_building, seat_floor, hired_at, source_updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (dis_id) DO UPDATE SET
            display_name      = EXCLUDED.display_name,
            email             = EXCLUDED.email,
            manager_id        = EXCLUDED.manager_id,
            team_id           = EXCLUDED.team_id,
            timezone          = EXCLUDED.timezone,
            seat_building     = EXCLUDED.seat_building,
            seat_floor        = EXCLUDED.seat_floor,
            hired_at          = EXCLUDED.hired_at,
            source_updated_at = EXCLUDED.source_updated_at
        WHERE employees.source_updated_at IS NULL
           OR EXCLUDED.source_updated_at > employees.source_updated_at
        RETURNING (xmax = 0) AS inserted
        """
        inserted = updated = skipped = 0
        async with self.db.acquire() as conn:
            async with conn.transaction():
                for r in records:
                    row = await conn.fetchrow(
                        query,
                        r.dis_id, r.display_name, r.email, r.manager_id,
                        r.team_id, r.timezone, r.seat_building, r.seat_floor,
                        r.hired_at, r.source_updated_at,
                    )
                    if row is None:
                        skipped += 1
                    elif row["inserted"]:
                        inserted += 1
                    else:
                        updated += 1
        return {"inserted": inserted, "updated": updated, "skipped": skipped}

    async def _upsert_skills(self, records: list[SkillRecord]) -> dict:
        if not records:
            return {"inserted": 0, "updated": 0, "skipped": 0}

        query = """
        INSERT INTO skills (dis_id, skill, level, source_updated_at)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (dis_id, skill) DO UPDATE SET
            level             = EXCLUDED.level,
            source_updated_at = EXCLUDED.source_updated_at
        WHERE skills.source_updated_at IS NULL
           OR EXCLUDED.source_updated_at > skills.source_updated_at
        RETURNING (xmax = 0) AS inserted
        """
        inserted = updated = skipped = 0
        async with self.db.acquire() as conn:
            async with conn.transaction():
                for r in records:
                    row = await conn.fetchrow(
                        query, r.dis_id, r.skill, r.level, r.source_updated_at
                    )
                    if row is None:
                        skipped += 1
                    elif row["inserted"]:
                        inserted += 1
                    else:
                        updated += 1
        return {"inserted": inserted, "updated": updated, "skipped": skipped}

    async def _upsert_presence(self, records: list[PresenceRecord]) -> dict:
        if not records:
            return {"inserted": 0, "updated": 0, "skipped": 0}

        query = """
        INSERT INTO presence (dis_id, status, last_seen_at, source, source_updated_at)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (dis_id) DO UPDATE SET
            status            = EXCLUDED.status,
            last_seen_at      = EXCLUDED.last_seen_at,
            source            = EXCLUDED.source,
            source_updated_at = EXCLUDED.source_updated_at
        WHERE presence.source_updated_at IS NULL
           OR EXCLUDED.source_updated_at > presence.source_updated_at
        RETURNING (xmax = 0) AS inserted
        """
        inserted = updated = skipped = 0
        async with self.db.acquire() as conn:
            async with conn.transaction():
                for r in records:
                    row = await conn.fetchrow(
                        query, r.dis_id, r.status, r.last_seen_at,
                        r.source, r.source_updated_at,
                    )
                    if row is None:
                        skipped += 1
                    elif row["inserted"]:
                        inserted += 1
                    else:
                        updated += 1
        return {"inserted": inserted, "updated": updated, "skipped": skipped}


# =============================================================================
# FAKE PROVIDERS
# =============================================================================
#
# Useful for tests, local development, and demoing the server without real
# Slack/Workday credentials. Also a template for real adapters.

class FakeHrisProvider:
    """Returns a fixed set of employees. Ignores `since`.

    Use this in tests when you want deterministic input, or in local dev
    when you want a running sync loop but don't have HRIS credentials.
    """

    def __init__(self, employees: list[EmployeeRecord], skills: list[SkillRecord]):
        self._employees = employees
        self._skills = skills

    async def fetch_employees(self, since: datetime | None) -> list[EmployeeRecord]:
        return list(self._employees)

    async def fetch_skills(self, since: datetime | None) -> list[SkillRecord]:
        return list(self._skills)


class FakePresenceProvider:
    """Rotates presence status across employees on every call.

    Handy for watching the presence resource change in real time during
    a demo: Alice goes active → away → dnd → offline → active.
    """

    _STATUSES = ("active", "away", "dnd", "offline")

    def __init__(self, dis_ids: list[str]):
        self._dis_ids = dis_ids
        self._tick = 0

    async def fetch_presence(self, since: datetime | None) -> list[PresenceRecord]:
        self._tick += 1
        now = datetime.now(timezone.utc)
        return [
            PresenceRecord(
                dis_id=dis_id,
                status=self._STATUSES[(self._tick + i) % len(self._STATUSES)],
                source="fake",
                last_seen_at=now,
                source_updated_at=now,
            )
            for i, dis_id in enumerate(self._dis_ids)
        ]