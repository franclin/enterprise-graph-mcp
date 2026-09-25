"""Enterprise Graph & Directory MCP Server.

Exposes corporate directory data, team topologies, and presence
to LLM clients via the Model Context Protocol.
"""
import argparse
import logging
import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

import anyio
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from enterprise_graph_mcp.api import build_router

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ResourceNotFoundError
from mcp.types import PromptMessage, TextContent, ListResourcesResult, PaginatedRequestParams, Resource as TypeResource

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from enterprise_graph_mcp.db import Database
from enterprise_graph_mcp.models import (
    BuddyPairing,
    PeerMatch,
    PresenceInfo,
    TeamTopology,
)
from enterprise_graph_mcp.ranking import generate_buddy_pairings
from enterprise_graph_mcp.piimaskingprocessor import PiiMaskingProcessor
from enterprise_graph_mcp.tokencostauditprocessor import TokenCostAuditProcessor

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource

# 1. Initialize OTel SDK configuration FIRST
resource = Resource(attributes={"service.name": "my-mcp-tool-server"})
provider = TracerProvider(resource=resource)

# Add the Masking processor FIRST so that the data is scrubbed before it hits the exporter. 
# This ensures that any PII is masked before being sent to the backend.
processor = PiiMaskingProcessor()
provider.add_span_processor(processor)


# Add the Token Cost Audit processor SECOND so that the data is audited after it's scrubbed.
audit_processor = TokenCostAuditProcessor()
provider.add_span_processor(audit_processor)


# Set it globally so the MCP SDK intercepts it
trace.set_tracer_provider(provider)

logger = logging.getLogger(__name__)
DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 500

# =============================================================================
# CONFIGURATION
# =============================================================================

class Settings(BaseSettings):
    """Configuration loaded from environment variables (prefixed EGD_)."""

    model_config = SettingsConfigDict(env_prefix="EGD_")
    database_url: str = "postgresql://postgres:postgres@localhost:5432/enterprise_graph"
    sync_interval_seconds: int = 300


settings = Settings()

# =============================================================================
# LIFESPAN
# =============================================================================

@asynccontextmanager
async def rest_lifespan(app: FastAPI) -> AsyncGenerator[dict, None]:
    db = Database(settings.database_url)
    await db.connect()
    app.state.db = db

    global _live_db
    _live_db = db

    # Inject a task group into the MCP session manager
    async with anyio.create_task_group() as tg:
        mcp.session_manager._task_group = tg
        try:
            yield {"db": db}
        finally:
            await db.disconnect()
            _live_db = None

# =============================================================================
# SERVER
# =============================================================================

mcp = MCPServer(
    "Enterprise Graph & Directory"
)

def _db(ctx: Context) -> Database:
    """Return the shared Database.

    The MCP server no longer owns a lifespan — the outer FastAPI app
    does. We resolve through the module-level `_live_db` instead of
    ctx.request_context.lifespan_context.
    """
    if _live_db is None:
        raise RuntimeError("Database not initialized")
    return _live_db

# =============================================================================
# RESOURCES
# =============================================================================

@mcp.resource("company://teams/{team_id}/topology")
async def team_topology(team_id: str, ctx: Context) -> TeamTopology:
    """Get the reporting topology and cross-team edges for a team.

    Returns JSON data showing who reports to whom within the team,
    along with any dotted-line reporting relationships and project
    assignments that cross team boundaries.
    """
    result = await _db(ctx).get_team_topology(team_id)
    if not result:
        raise ResourceNotFoundError(f"Team not found: {team_id}")
    return TeamTopology(**result)


@mcp.resource("employee://{dis_id}/presence")
async def employee_presence(dis_id: str, ctx: Context) -> PresenceInfo:
    """Get live availability and local time for an employee.

    Returns the employee's current presence status (from chat/calendar
    integration), their local time, timezone, and working hours.
    """
    result = await _db(ctx).get_employee_presence(dis_id)
    if not result:
        raise ResourceNotFoundError(f"Employee not found: {dis_id}")
    return PresenceInfo(**result)


# =============================================================================
# TOOLS — Discovery
# =============================================================================

@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def list_teams(
    limit: Annotated[
        int,
        Field(
            ge=1,
            le=MAX_PAGE_SIZE,
            description=f"Max rows per page (default {DEFAULT_PAGE_SIZE}, max {MAX_PAGE_SIZE}).",
        ),
    ] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[
        str | None,
        Field(
            description="Opaque pagination cursor from a previous call's next_cursor. "
                        "Omit to get the first page."
        ),
    ] = None,
    ctx: Context = None,
) -> ListResourcesResult:
    """List teams as browsable resources.

    Call this before reading company://teams/{team_id}/topology to
    discover valid team_id values. Each result's uri can be read directly.

    Results are paginated — if the response's next_cursor is not null, call
    again with cursor=next_cursor for more.
    """
    db = _db(ctx)
    query = """
        SELECT DISTINCT team_id
        FROM employees
        WHERE ($1::text IS NULL OR team_id > $1)
        ORDER BY team_id
        LIMIT $2
    """
    rows = await db.acquire_fetch(query, cursor, limit + 1)

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1]["team_id"] if has_more else None

    resources = [
        TypeResource(
            uri=f"company://teams/{r['team_id']}/topology",
            name=r["team_id"],
            description=f"Team {r['team_id']} topology",
            mime_type="application/json",
        )
        for r in page
    ]
    return ListResourcesResult(resources=resources, next_cursor=next_cursor)

@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def list_employees(
    team_id: Annotated[
        str | None,
        Field(description="Optional team filter (e.g., 'platform'). "
                          "NOTE: an unknown team_id returns an empty list, "
                          "not an error — call list_teams first to verify."),
    ] = None,
    limit: Annotated[
        int,
        Field(ge=1, le=MAX_PAGE_SIZE, description=f"Max rows per page (default {DEFAULT_PAGE_SIZE}, max {MAX_PAGE_SIZE})."),
    ] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[
        str | None,
        Field(description="Opaque pagination cursor from a previous call's next_cursor. "
                          "Omit to get the first page."),
    ] = None,
    ctx: Context = None,
) -> ListResourcesResult:
    """List employees as browsable resources, optionally filtered by team.
 
    Results are paginated — if the response's next_cursor is not null, call
    again with cursor=next_cursor for more. Each result's uri can be read
    directly, e.g. employee://{dis_id}/presence, to check availability.
 
    Args:
        team_id: Optional team filter (e.g., 'platform').
        limit: Max rows per page.
        cursor: Pagination cursor from a previous response, or None for the first page.
    """
    db = _db(ctx)
    query = """
        SELECT dis_id, display_name, team_id, timezone
        FROM employees
        WHERE ($1::text IS NULL OR team_id = $1)
          AND ($2::text IS NULL OR dis_id > $2)
        ORDER BY dis_id
        LIMIT $3
    """
    rows = await db.acquire_fetch(query, team_id, cursor, limit + 1)
 
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1]["dis_id"] if has_more else None
 
    resources = [
        TypeResource(
            uri=f"employee://{r['dis_id']}/presence",
            name=r["display_name"],
            description=f"team={r['team_id']}, tz={r['timezone']}",
            mime_type="application/json",
        )
        for r in page
    ]
    return ListResourcesResult(resources=resources, next_cursor=next_cursor)


# =============================================================================
# TOOLS — Recommendations
# =============================================================================

@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def find_cross_functional_peers(
    skill: str,
    timezone: str,
    exclude_team_id: str | None = None,
    max_results: int = 5,
    ctx: Context = None,
) -> list[PeerMatch]:
    """Find employees with a given skill, ranked by timezone overlap
    and cross-team diversity.

    Use this to identify the right internal engineer to loop into
    a project. The ranking considers skill proficiency, working-hours
    overlap with the requested timezone, and penalizes candidates
    from the same team (encouraging cross-functional collaboration).

    Args:
        skill: Normalized skill name (e.g., 'python', 'kubernetes', 'rust').
        timezone: IANA timezone for overlap calculation (e.g., 'Europe/Dublin').
        exclude_team_id: Optional team to exclude (typically the requester's team).
        max_results: Maximum number of candidates to return (default 5).
    """
    peers = await _db(ctx).find_peers_by_skill(
        skill=skill,
        timezone=timezone,
        exclude_team_id=exclude_team_id,
        max_results=max_results,
    )
    return [PeerMatch(**p) for p in peers]


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def generate_onboarding_buddy_pairings(
    new_hire_id: Annotated[str, Field(min_length=1, description="The dis_id of the new hire to find a buddy for.")],
    cohort_size: Annotated[int, Field(ge=1, le=10, description="Number of buddy candidates to consider (1-10).")] = 1,
    ctx: Context = None,
) -> list[BuddyPairing]:
    """Match new hires with veteran onboarding buddies.

    Matches based on team proximity, tech stack overlap, and seat
    adjacency (same building/floor preferred). Buddies must have
    at least 6 months tenure and cannot be the new hire's direct manager.

    Args:
        new_hire_id: The dis_id of the new hire to find a buddy for.
        cohort_size: Number of buddy candidates to consider (default 1).
    """
    db = _db(ctx)

    candidates = await db.get_onboarding_candidates(new_hire_id)
    if not candidates:
        return []

    new_hire = await db.acquire_fetchrow(
        "SELECT * FROM employees WHERE dis_id = $1", new_hire_id
    )
    if not new_hire:
        raise ResourceNotFoundError(f"New hire not found: {new_hire_id}")

    if ctx is not None:
        await ctx.report_progress(0.3, "Fetching buddy candidates")

    new_hire_skills = await db.acquire_fetch(
        "SELECT skill FROM skills WHERE dis_id = $1", new_hire_id
    )

    if ctx is not None:
        await ctx.report_progress(0.7, "Scoring candidates")

    pairings = generate_buddy_pairings(
        new_hire=dict(new_hire),
        new_hire_skills=[s["skill"] for s in new_hire_skills],
        candidates=candidates,
        cohort_size=cohort_size,
    )
    return [BuddyPairing(**p) for p in pairings]

# =============================================================================
# TOOLS — Prompting
@mcp.prompt()
async def staffing_plan(
    project_description: str,
    required_skills: str,
    target_timezone: str = "Europe/Dublin",
    team_size: int = 3,
) -> list[PromptMessage]:
    """Draft a staffing plan for a project.

    Produces a guided workflow for identifying cross-functional
    contributors, checking their availability, and proposing a team.

    Args:
        project_description: What the project is about.
        required_skills: Comma-separated list of skills needed.
        target_timezone: Primary working timezone for the team.
        team_size: Approximate number of people to staff.
    """
    skills_list = [s.strip() for s in required_skills.split(",") if s.strip()]

    return [
        PromptMessage(
            role="user",
            content=TextContent(
                type="text",
                text=f"""I need to staff a project: **{project_description}**

Required skills: {', '.join(skills_list)}
Target timezone: {target_timezone}
Approximate team size: {team_size}

Please help me build a staffing plan by following these steps:

1. Use the `find_cross_functional_peers` tool once per required skill
   ({', '.join(skills_list)}), with timezone={target_timezone}.
   Aim for 2-3 candidates per skill.

2. For each shortlisted candidate, read the `employee://{{dis_id}}/presence`
   resource to check current availability. Flag anyone who is `offline` or
   `dnd` as a scheduling risk.

3. If a candidate's team looks relevant, read
   `company://teams/{{team_id}}/topology` to understand their reporting
   structure and who else might be available from the same team.

4. Produce a final proposal with:
   - The recommended team (up to {team_size} people), with a one-line
     rationale per person
   - A timezone coverage summary (who covers which hours)
   - Any scheduling risks or gaps you identified
   - Suggested next steps (who to contact first, what to confirm)

Be explicit about which tool or resource you used to reach each
conclusion."""
            ),
        ),
    ]

@mcp.prompt()
async def onboarding_plan(new_hire_id: str) -> list[PromptMessage]:
    """Build a first-week onboarding plan for a new hire.

    Produces a structured plan covering introductions, buddy assignment,
    and initial meetings based on the new hire's team and location.

    Args:
        new_hire_id: The dis_id of the new hire (e.g., 'iris').
    """
    return [
        PromptMessage(
            role="user",
            content=TextContent(
                type="text",
                text=f"""Please build a first-week onboarding plan for the new hire
with dis_id `{new_hire_id}`.

Steps to follow:

1. Read the resource `employee://{new_hire_id}/presence` to get their
   name, timezone, and working hours.

2. Call `list_employees` to find their teammates. Then read
   `company://teams/{{team_id}}/topology` for the new hire's team to
   understand the reporting structure.

3. Call `generate_onboarding_buddy_pairings` with
   new_hire_id="{new_hire_id}" to get a buddy recommendation.

4. Produce a plan with these sections:
   - **Welcome summary**: name, team, timezone, manager
   - **Buddy**: who they're paired with and why (use the tool's reason)
   - **Day 1 introductions**: 3-5 people they should meet, with a
     one-line note about why each matters (draw from the topology)
   - **Week 1 goals**: a short checklist appropriate for their team
   - **Scheduling notes**: any timezone considerations for meetings

Every name you include must come from a tool call or resource read.
Do not invent people."""
            ),
        ),
    ]

@mcp.prompt()
async def find_expert(
    topic: str,
    timezone: str = "Europe/Dublin",
) -> list[PromptMessage]:
    """Find the right internal expert to consult on a topic.

    Args:
        topic: The subject the user needs help with (e.g., 'rust', 'kubernetes').
        timezone: The requester's timezone, for overlap calculation.
    """
    return [
        PromptMessage(
            role="user",
            content=TextContent(
                type="text",
                text=f"""Who in the company should I talk to about **{topic}**?

I'm in the {timezone} timezone.

Use `find_cross_functional_peers` with skill="{topic}" and
timezone="{timezone}" to find candidates. Then, for the top result,
read `employee://{{dis_id}}/presence` to check whether they're
currently available.

Give me:
- The single best person to contact, with a one-sentence reason
- One backup option in case the first is unavailable
- A suggested opening message I could send them, referencing why
  they're a good fit

Keep it short — I just want an answer I can act on."""
            ),
        ),
    ]

@mcp.prompt()
async def timezone_coverage(team_id: str) -> list[PromptMessage]:
    """Analyze timezone coverage for a team.

    Args:
        team_id: The team to analyze (e.g., 'platform').
    """
    return [
        PromptMessage(
            role="user",
            content=TextContent(
                type="text",
                text=f"""Analyze the timezone coverage for the `{team_id}` team.

Steps:

1. Read `company://teams/{team_id}/topology` to list all team members.

2. For each member, read `employee://{{dis_id}}/presence` to get
   their timezone and local time.

3. Produce:
   - A table of members with timezone, local time, and current status
   - A timeline visualization showing overlapping working hours
     (assume 09:00-17:30 local for everyone)
   - The best 2-hour window for a team meeting that includes
     the most people
   - Any members whose working hours have zero overlap with the rest
     of the team, and a suggested accommodation"""
            ),
        ),
    ]

# =============================================================================
# ENTRYPOINT
# =============================================================================
class _LazyDB:
    """Proxy that resolves the real Database at call time.

    Lets us build the REST router at import time even though the real
    Database isn't created until the lifespan starts.
    """

    def __getattr__(self, name):
        if _live_db is None:
            raise RuntimeError("Database not initialized")
        return getattr(_live_db, name)

def _build_http_app() -> FastAPI:
    """Return a combined ASGI app: MCP transport + REST + health."""
    rest = FastAPI(lifespan=rest_lifespan)
    rest.include_router(build_router(_LazyDB()))

    @rest.get("/health")
    async def health():
        """Liveness + readiness probe: verifies the DB pool actually works."""
        if _live_db is None:
            return JSONResponse(
                {"status": "starting", "database": "not initialized"},
                status_code=503,
            )
        try:
            await asyncio.wait_for(_live_db.acquire_fetchrow("SELECT 1"), timeout=2.0)
        except Exception:
            logger.exception("health check failed: database unreachable")
            return JSONResponse(
                {"status": "unhealthy", "database": "down"},
                status_code=503,
            )
        return {"status": "healthy", "database": "ok"}

    # Mount the MCP Streamable HTTP app under /mcp
    mcp_app = mcp.streamable_http_app()
    rest.mount("/", mcp_app)
    return rest

def main() -> None:
    """Parse CLI args and run the server with the requested transport."""
    parser = argparse.ArgumentParser(description="Enterprise Graph & Directory MCP Server")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "starting MCP server transport=%s host=%s port=%s",
        args.transport, args.host, args.port,
    )

    if args.transport == "streamable-http":
        import uvicorn

        app = _build_http_app()
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        # stdio and sse run through the SDK's built-in runner.
        mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()