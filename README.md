# Enterprise Graph & Directory MCP Server

An MCP server that exposes corporate directory data, team topologies, and live presence to LLM clients. Built for the enterprise problem of siloed employee data — org charts, chat presence, and project assignments that live in separate systems and never talk to each other.

> **Status:** reference implementation. Ships with seeded sample data, a React console, OpenTelemetry instrumentation with PII masking, and paginated discovery tools. Not production-hardened — see [Security & Limitations](#security--limitations).

---

## Table of Contents

- [What It Does](#what-it-does)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [MCP Surface](#mcp-surface)
  - [Resources](#resources)
  - [Tools](#tools)
  - [Prompts](#prompts)
- [REST API](#rest-api)
- [Web Console](#web-console)
- [Observability](#observability)
- [Development](#development)
- [Testing](#testing)
- [Configuration](#configuration)
- [Security & Limitations](#security--limitations)
- [Roadmap](#roadmap)
- [License](#license)

---

## What It Does

Large organizations struggle with a specific class of question: *"Who should I loop into this project?"* Answering it requires joining data that lives in different systems — the HRIS knows the org chart, Slack knows who's online, Jira knows who's worked on what, and the facilities database knows where everyone sits.

This server unifies those signals behind a single MCP interface so LLMs can:

- **Find cross-functional peers** by skill, ranked by timezone overlap and cross-team diversity.
- **Pair new hires with onboarding buddies** based on team proximity, tech stack, and seat adjacency.
- **Inspect team topologies** — who reports to whom, across which teams.
- **Check live presence** — status, local time, and working hours, computed server-side so the model never does timezone math.

The project is a working demonstration of MCP primitives (resources, tools, prompts), PostgreSQL query optimization with closure tables, and production concerns like pagination, observability, and structured error handling.

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  HRIS (Workday) │     │  Chat (Slack)    │     │  PM (Jira)       │
│  org chart      │     │  presence        │     │  projects/skills │
└────────┬────────┘     └────────┬─────────┘     └────────┬─────────┘
         │                       │                        │
         └───────────┬───────────┴────────────┬───────────┘
                     ▼                        ▼
            ┌────────────────────────────────────────┐
            │  Sync workers (async, idempotent)      │
            │  upsert employees, edges, presence     │
            └──────────────────┬─────────────────────┘
                               ▼
            ┌────────────────────────────────────────┐
            │  PostgreSQL                            │
            │  employees, reporting_closure,         │
            │  teams, skills, projects, presence,    │
            │  seat_map (building/floor/timezone)    │
            └──────────────────┬─────────────────────┘
                               ▼
            ┌────────────────────────────────────────┐
            │  FastAPI + MCP SDK (Streamable HTTP)   │
            │  - /mcp       MCP transport            │
            │  - /api       REST shim for the web UI │
            │  - /health    liveness + DB check      │
            └──────────────────┬─────────────────────┘
                               │
                ┌──────────────┴──────────────┐
                ▼                             ▼
        ┌───────────────┐             ┌───────────────┐
        │  MCP clients  │             │  React/Vite   │
        │  (Inspector,  │             │  web console  │
        │   Claude, …)  │             │               │
        └───────────────┘             └───────────────┘
```

**Design notes:**

- **Closure table** (`reporting_closure`) materializes the reporting hierarchy so org-chart traversal is a single indexed join instead of a recursive CTE. Refreshed incrementally on manager changes.
- **Server-side timezone math.** Presence responses include a pre-computed `local_time` in ISO 8601 with offset. The LLM never does timezone arithmetic.
- **Single `Database` pool** shared between the MCP handlers and the REST shim. The FastAPI lifespan owns the pool; the MCP session manager runs inside it.
- **PII masking at the span level.** An OpenTelemetry `SpanProcessor` scrubs emails, employee IDs, and names from span attributes before they reach any exporter.

---

## Quick Start

### Prerequisites

- Docker and Docker Compose
- (Optional, for local dev) Python 3.11–3.13 and `uv`
- (Optional, for local dev) Node 20+

### Run the full stack

```bash
git clone https://github.com/franclin/enterprise-graph-mcp.git
cd enterprise-graph-mcp
docker compose up --build
```

This starts four services:

| Service | Port | Purpose |
|---|---|---|
| `server` | 8000 | MCP transport (`/mcp`), REST API (`/api`), health check (`/health`) |
| `frontend` | 5173 | React web console |
| `db` | 5432 | PostgreSQL 16 |
| `jaeger` | 16686 | Trace UI |

### Seed the database

The first time you run the stack, seed sample data:

```bash
docker compose exec server python scripts/seed.py
```

Sample data includes 9 employees across 4 teams (`platform`, `frontend`, `backend`, `data`), with skills, presence, and a reporting hierarchy.

### Verify

```bash
# Health check
curl http://localhost:8000/health
# → {"status":"healthy","database":"ok"}

# MCP transport — initialize handshake
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'

# REST API
curl http://localhost:8000/api/teams
# → [{"team_id":"backend"},{"team_id":"data"},…]
```

Open http://localhost:5173 for the web console and http://localhost:16686 for Jaeger.

---

## MCP Surface

### Resources

Read-only, deterministic, cacheable. URIs use a custom scheme.

| URI | Returns |
|---|---|
| `company://teams/{team_id}/topology` | Reporting structure for a team, plus cross-team edges |
| `employee://{dis_id}/presence` | Live status, local time, timezone, working hours |

**Example:**

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc":"2.0","id":1,"method":"resources/read",
    "params":{"uri":"employee://alice/presence"}
  }'
```

### Tools

Computation and recommendations. All tools are annotated with `readOnlyHint: true` and `destructiveHint: false`.

| Tool | Purpose |
|---|---|
| `list_teams` | Paginated list of teams as browsable resources |
| `list_employees` | Paginated list of employees, optionally filtered by team |
| `find_cross_functional_peers` | Rank peers by skill, timezone overlap, and cross-team diversity |
| `generate_onboarding_buddy_pairings` | Match new hires with veteran buddies |

**Ranking for `find_cross_functional_peers`:**

| Signal | Weight |
|---|---|
| Skill proficiency (normalized 1–5) | 50% |
| Timezone overlap (working hours) | 30% |
| Cross-team bonus | 20% |

Every result carries a `reason` field explaining the score — the LLM picks better when it can see the rationale.

**Pagination:** `list_teams` and `list_employees` return `ListResourcesResult` with an opaque `next_cursor`. Pass it back as `cursor` to page. Default page size 200, max 500.

**Progress reporting:** `generate_onboarding_buddy_pairings` emits `report_progress` notifications at 30% and 70%, useful for long-running clients.

### Prompts

Pre-written templates that chain tools and resources into workflows. Clients surface these as slash commands or suggested actions.

| Prompt | Workflow |
|---|---|
| `staffing_plan` | Given a project and skills, produce a staffing proposal with rationale |
| `onboarding_plan` | Given a new hire, produce a first-week plan grounded in real directory data |
| `find_expert` | Find the right internal expert on a topic |
| `timezone_coverage` | Analyze team timezone overlap and suggest a meeting window |

Each prompt is prescriptive: it names the specific tools to call, the output format, and a grounding clause ("every name must come from a tool call") to prevent hallucinated employees.

**Example:**

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc":"2.0","id":1,"method":"prompts/get",
    "params":{
      "name":"find_expert",
      "arguments":{"topic":"rust","timezone":"Europe/Dublin"}
    }
  }'
```

---

## REST API

A thin REST shim over the same `Database` methods the MCP handlers use. This exists so the React console doesn't need to speak MCP — one source of truth, two interfaces.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness + database reachability (returns 503 if DB is down) |
| `GET` | `/api/teams` | List all teams |
| `GET` | `/api/employees?team_id=` | List employees, optionally filtered |
| `GET` | `/api/teams/{team_id}/topology` | Team topology |
| `GET` | `/api/employees/{dis_id}/presence` | Employee presence |
| `POST` | `/api/tools/find-cross-functional-peers` | Ranked peer search |
| `POST` | `/api/tools/onboarding-buddy-pairings` | Buddy recommendation |

---

## Web Console

A React + Vite + Tailwind app for exploring the directory without an MCP client. Four tabs:

- **Team Topology** — pick a team, see members and reporting structure
- **Presence** — pick an employee, see live status and local time
- **Find Peers** — enter a skill and timezone, get ranked cross-functional matches
- **Onboarding Buddy** — pick a new hire, get buddy recommendations with score and rationale

In dev mode, Vite proxies `/api` to `localhost:8000`. In Docker, nginx serves the built app and reverse-proxies to the `server` container.

---

## Observability

Traces flow to Jaeger over OTLP gRPC. The service name is `my-mcp-tool-server`.

**Two custom `SpanProcessor`s run in order:**

1. **`PiiMaskingProcessor`** — scrubs emails, employee IDs, and display names from span attributes *before* they reach any exporter. Runs first so nothing sensitive leaves the process.
2. **`TokenCostAuditProcessor`** — records token-cost metadata for LLM-adjacent spans. Runs second, on already-scrubbed data.

**View traces:**

```
http://localhost:16686
```

Select `my-mcp-tool-server` in the service dropdown.

**Endpoint configuration:**

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317
```

Set in `compose.yaml`. Note the port is **4317** (OTLP gRPC), not 16686 (Jaeger UI). The UI is read-only; the SDK sends traces to 4317.

---

## Development

### Local setup

```bash
uv sync
uv python pin 3.12

# Start Postgres and Jaeger only
docker compose up db jaeger -d

# Seed
uv run python scripts/seed.py

# Run the server
uv run python -m enterprise_graph_mcp.server \
  --transport streamable-http --host 0.0.0.0 --port 8000
```

### Frontend dev

```bash
cd frontend
npm install
npm run dev
```

Vite serves on `http://localhost:5173` and proxies `/api` and `/health` to the backend.

### Project layout

```
enterprise-graph-mcp/
├── src/enterprise_graph_mcp/
│   ├── server.py                   # MCP server, FastAPI app, entrypoint
│   ├── api.py                      # REST shim for the web console
│   ├── db.py                       # asyncpg pool + query layer
│   ├── models.py                   # Pydantic structured output
│   ├── ranking.py                  # peer + buddy scoring
│   ├── piimaskingprocessor.py      # OTel span processor — scrubs PII
│   └── tokencostauditprocessor.py  # OTel span processor — cost audit
├── frontend/                       # React + Vite + Tailwind console
├── scripts/seed.py                 # schema + sample data
├── Dockerfile
├── compose.yaml
└── pyproject.toml
```

### Interacting with the running server

**MCP Inspector (visual):**

```bash
npx @modelcontextprotocol/inspector --server-url http://localhost:8000/mcp --transport http
```

**MCP Inspector (CLI):**

```bash
npx @modelcontextprotocol/inspector --cli http://localhost:8000/mcp \
  --method tools/list
```

**Raw curl** (matches the MCP spec exactly, no CLI wrapper quirks):

```bash
curl -s -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | jq .
```

---

## Testing

### Unit tests

Test the ranking logic and query layer directly, without going through MCP:

```bash
uv run pytest
```

### Integration tests

The MCP SDK's client can connect to your `MCPServer` instance in-process:

```python
from mcp.client import ClientSession
from enterprise_graph_mcp.server import mcp

async with ClientSession(mcp) as session:
    await session.initialize()
    tools = await session.list_tools()
    assert any(t.name == "find_cross_functional_peers" for t in tools.tools)
```

### Evaluation

For retrieval quality (does `find_cross_functional_peers` return good candidates?) and agent effectiveness (does an LLM produce better answers with MCP than without?), see the [Roadmap](#roadmap). The evaluation harness is planned but not yet implemented.

---

## Configuration

All settings come from environment variables prefixed `EGD_`, loaded via `pydantic-settings`.

| Variable | Default | Purpose |
|---|---|---|
| `EGD_DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/enterprise_graph` | PostgreSQL connection string |
| `EGD_SYNC_INTERVAL_SECONDS` | `300` | Background sync cadence (stub) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP gRPC collector |
| `OTEL_SERVICE_NAME` | `my-mcp-tool-server` | Service name in Jaeger |

---

## Security & Limitations

This is a **reference implementation**, not a production system. Known gaps:

- **No authentication or authorization.** Every MCP client and REST caller sees every employee. In production you need OAuth on the MCP transport and row-level scoping so users only see their own org's data.
- **Presence and seat data is PII.** The `PiiMaskingProcessor` scrubs span attributes, but the DB itself stores unmasked data and API responses return it verbatim. Pseudonymize in non-production environments; enforce authz in production.
- **Timezone overlap is a heuristic.** The `find_peers_by_skill` query approximates overlap as "8h if same timezone, 4h otherwise." Real overlap requires per-timezone working-hour tables. Documented inline as a TODO.
- **Skill matching is exact-string.** No synonyms, no adjacency ("k8s" ≠ "kubernetes"). A production system would need a controlled vocabulary or embedding-based match.
- **Sync workers are stubs.** The `sync_interval_seconds` setting exists but no background task reads from HRIS/Slack/Jira. Seed data is the only source.
- **No rate limiting.** The MCP transport will happily serve unbounded requests. Add `slowapi` or an ingress-level limiter before exposing publicly.

---

## Roadmap

- [ ] **Evaluation harness.** Golden sets for retrieval (recall@k, MRR, NDCG) and agent effectiveness (task success rate, tool-call efficiency, hallucination rate).
- [ ] **Real sync adapters.** Pluggable `PresenceProvider`, `HrisProvider`, `ProjectProvider` interfaces with Slack, Workday, and Jira implementations.
- [ ] **RebAC.** OpenFGA or similar for "can this user see this employee's record?"
- [ ] **Time-zone overlap table.** Replace the heuristic with real per-timezone working-hour intersections.
- [ ] **Skill vocabulary.** Controlled vocabulary + synonym resolution, or embedding-based fuzzy match.
- [ ] **Graph DB variant.** Mirror the closure table into Neo4j and benchmark traversal.
- [ ] **Auth.** OAuth on MCP, JWT on REST, with per-user scoping.

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

## Acknowledgments

Built with:

- [Model Context Protocol](https://modelcontextprotocol.io/) — the SDK and spec
- [FastAPI](https://fastapi.tiangolo.com/) — REST and ASGI hosting
- [asyncpg](https://github.com/MagicStack/asyncpg) — PostgreSQL driver
- [OpenTelemetry](https://opentelemetry.io/) — tracing
- [Jaeger](https://www.jaegertracing.io/) — trace backend
- [Vite](https://vitejs.dev/) + [React](https://react.dev/) + [Tailwind](https://tailwindcss.com/) — web console