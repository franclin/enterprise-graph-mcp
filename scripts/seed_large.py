"""Seed the database with a large, realistic enterprise org.

Reuses SCHEMA_SQL from seed.py (keep both files in the same directory).

Generates a CEO -> VP -> team head -> manager -> IC hierarchy, a *complete*
reporting_closure (every ancestor/descendant pair, not just direct reports),
skills weighted by team, and presence rows.

Usage:
    python seed_large.py                       # 5,000 employees
    python seed_large.py -n 50000 --truncate   # wipe tables first, 50k people
    python seed_large.py -n 2000 --dry-run     # generate + print stats, no DB
"""
import argparse
import asyncio
import os
import math
import random
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import asyncpg

from seed import SCHEMA_SQL

# DSN = "postgresql://postgres:postgres@localhost:5432/enterprise_graph"
DSN = os.getenv("EGD_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/enterprise_graph")

# location -> (timezone, floors)
LOCATIONS = {
    "HQ": ("Europe/Dublin", 6),
    "London": ("Europe/London", 4),
    "NYC": ("America/New_York", 5),
    "SF": ("America/Los_Angeles", 4),
    "Austin": ("America/Chicago", 3),
    "Berlin": ("Europe/Berlin", 3),
    "Bangalore": ("Asia/Kolkata", 5),
    "Singapore": ("Asia/Singapore", 3),
    "Tokyo": ("Asia/Tokyo", 2),
    "Sydney": ("Australia/Sydney", 2),
}
REMOTE_TZS = [tz for tz, _ in LOCATIONS.values()] + [
    "America/Denver", "America/Sao_Paulo", "Europe/Warsaw", "Africa/Lagos",
]

# team -> (division, weight, home locations, skill pool)
TEAMS = {
    "platform":  ("eng",   6, ["HQ", "NYC"],          ["python", "kubernetes", "postgresql", "terraform", "go"]),
    "frontend":  ("eng",   5, ["HQ", "London"],       ["typescript", "react", "css", "graphql", "webpack"]),
    "backend":   ("eng",   8, ["Tokyo", "HQ", "SF"],  ["rust", "python", "go", "postgresql", "grpc", "java"]),
    "sre":       ("eng",   4, ["HQ", "Bangalore"],    ["kubernetes", "terraform", "prometheus", "linux", "python"]),
    "mobile":    ("eng",   4, ["Berlin", "SF"],       ["swift", "kotlin", "react-native", "typescript"]),
    "payments":  ("eng",   5, ["NYC", "London"],      ["java", "postgresql", "kafka", "python", "go"]),
    "search":    ("eng",   3, ["SF", "Berlin"],       ["java", "elasticsearch", "python", "rust"]),
    "devtools":  ("eng",   3, ["Austin", "HQ"],       ["rust", "go", "typescript", "bazel", "python"]),
    "data":      ("data",  5, ["HQ", "London"],       ["python", "sql", "spark", "airflow", "dbt"]),
    "ml":        ("data",  5, ["SF", "London", "Bangalore"], ["python", "pytorch", "sql", "cuda", "mlops"]),
    "analytics": ("data",  3, ["NYC", "Singapore"],   ["sql", "tableau", "python", "dbt", "statistics"]),
    "security":  ("sec",   4, ["HQ", "Austin"],       ["python", "linux", "pentesting", "go", "cryptography"]),
    "compliance":("sec",   2, ["HQ", "NYC"],          ["sql", "auditing", "risk", "python"]),
    "design":    ("prod",  3, ["London", "NYC"],      ["figma", "css", "prototyping", "typescript"]),
    "product":   ("prod",  3, ["SF", "HQ"],           ["sql", "roadmapping", "analytics", "figma"]),
    "qa":        ("prod",  3, ["Bangalore", "HQ"],    ["python", "selenium", "typescript", "java"]),
    "support":   ("ops",   5, ["Sydney", "Singapore", "HQ"], ["sql", "zendesk", "python", "linux"]),
    "it":        ("ops",   3, ["HQ", "Austin"],       ["linux", "powershell", "okta", "networking"]),
    "finance":   ("ops",   2, ["NYC", "HQ"],          ["sql", "excel", "netsuite", "auditing"]),
    "recruiting":("ops",   2, ["HQ", "London"],       ["sourcing", "greenhouse", "excel"]),
}
GLOBAL_SKILLS = ["python", "sql", "typescript", "go", "linux", "kubernetes",
                 "postgresql", "java", "rust", "excel", "figma"]
DIVISIONS = ["eng", "data", "sec", "prod", "ops"]

FIRST = ["Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace", "Henry", "Iris",
         "Aoife", "Ciaran", "Niamh", "Sean", "Priya", "Arjun", "Wei", "Mei", "Hiro",
         "Yuki", "Fatima", "Omar", "Chidi", "Amara", "Lucas", "Sofia", "Mateo",
         "Elena", "Pavel", "Anya", "Liam", "Emma", "Noah", "Olivia", "Ravi", "Sana",
         "Tomasz", "Ingrid", "Jonas", "Marta", "Diego"]
LAST = ["Chen", "Murphy", "O'Brien", "Kim", "Patel", "Novak", "Liu", "Adeyemi",
        "Zhang", "Walsh", "Ryan", "Sharma", "Singh", "Tanaka", "Sato", "Nguyen",
        "Garcia", "Silva", "Kowalski", "Muller", "Rossi", "Ivanov", "Okafor",
        "Hassan", "Khan", "Byrne", "Kelly", "Doyle", "Fitzgerald", "Larsen",
        "Johansson", "Costa", "Lopez", "Brown", "Wilson", "Taylor", "Evans"]

MAX_SPAN = 9  # max direct reports per manager


class Org:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.rows: list[tuple] = []      # employees, parents always before children
        self.skill_rows: list[tuple] = []
        self.parent: dict[str, str | None] = {}
        self.level: dict[str, int] = {}  # seniority 1..5 for skill levels
        self.used_ids: set[str] = set()
        self.used_emails: set[str] = set()
        self.n = 0

    def _identity(self):
        first, last = self.rng.choice(FIRST), self.rng.choice(LAST)
        self.n += 1
        clean = last.replace("'", "").lower()
        dis_id = f"{first[0].lower()}{clean}{self.n}"
        email = f"{first.lower()}.{clean}{self.n}@corp.com"
        return dis_id, f"{first} {last}", email

    def add(self, team, manager, location, seniority, min_year=2015):
        rng = self.rng
        dis_id, name, email = self._identity()
        remote = rng.random() < 0.08
        if remote:
            tz, building, floor = rng.choice(REMOTE_TZS), None, None
        else:
            tz, floors = LOCATIONS[location]
            building, floor = location, rng.randint(1, floors)
        start = date(min_year, 1, 1)
        span = (date(2026, 8, 31) - start).days
        hired = start + timedelta(days=int(span * rng.random() ** 1.3))
        self.rows.append((dis_id, name, email, manager, team, tz, building, floor, hired))
        self.parent[dis_id] = manager
        self.level[dis_id] = seniority
        self._skills(dis_id, team, seniority)
        return dis_id

    def _skills(self, dis_id, team, seniority):
        rng = self.rng
        pool = TEAMS[team][3] if team in TEAMS else GLOBAL_SKILLS
        k = min(len(pool), rng.randint(2, 5))
        chosen = set(rng.sample(pool, k))
        if rng.random() < 0.35:
            chosen.add(rng.choice(GLOBAL_SKILLS))
        for skill in chosen:
            lvl = max(1, min(5, seniority + rng.choice([-2, -1, -1, 0, 0, 1])))
            self.skill_rows.append((dis_id, skill, lvl))


def build_org(total: int, seed: int) -> Org:
    org = Org(seed)
    rng = org.rng

    ceo = org.add("exec", None, "HQ", 5, min_year=2012)
    vps = {d: org.add("exec", ceo, rng.choice(["HQ", "NYC", "SF"]), 5, 2013) for d in DIVISIONS}

    remaining = max(0, total - 1 - len(vps))
    total_weight = sum(w for _, w, _, _ in TEAMS.values())

    for team, (division, weight, homes, _) in TEAMS.items():
        size = max(2, round(remaining * weight / total_weight))
        head = org.add(team, vps[division], homes[0], 5, 2014)
        members = size - 1
        n_managers = 0 if members <= MAX_SPAN else math.ceil(members / MAX_SPAN)
        managers = [org.add(team, head, rng.choice(homes), 4, 2016) for _ in range(n_managers)]
        members -= n_managers
        leaders = managers or [head]
        for i in range(max(0, members)):
            boss = leaders[i % len(leaders)]
            seniority = rng.choices([1, 2, 3, 4], weights=[2, 4, 3, 1])[0]
            org.add(team, boss, rng.choice(homes), seniority)
    return org


def build_closure(parent: dict[str, str | None]) -> list[tuple[str, str, int]]:
    """Full transitive closure: every (ancestor, descendant, depth), incl. self at 0."""
    rows = []
    for emp in parent:
        cur, depth = emp, 0
        while cur is not None:
            rows.append((cur, emp, depth))
            cur = parent[cur]
            depth += 1
    return rows


def build_presence(org: Org):
    rng = org.rng
    now = datetime.now(timezone.utc)
    rows = []
    for row in org.rows:
        dis_id = row[0]
        status = rng.choices(["active", "away", "dnd", "offline"], [55, 20, 10, 15])[0]
        minutes = {"active": 5, "away": 90, "dnd": 60, "offline": 60 * 24 * 3}[status]
        last_seen = now - timedelta(minutes=rng.expovariate(1 / minutes))
        source = rng.choices(["slack", "webex", "teams"], [80, 15, 5])[0]
        rows.append((dis_id, status, last_seen, source))
    return rows


async def load(org: Org, closure, presence, truncate: bool):
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(SCHEMA_SQL)
        async with conn.transaction():
            if truncate:
                await conn.execute(
                    "TRUNCATE presence, skills, reporting_closure, employees CASCADE"
                )
            await conn.copy_records_to_table(
                "employees", records=org.rows,
                columns=["dis_id", "display_name", "email", "manager_id", "team_id",
                         "timezone", "seat_building", "seat_floor", "hired_at"],
            )
            await conn.copy_records_to_table(
                "reporting_closure", records=closure,
                columns=["ancestor_id", "descendant_id", "depth"],
            )
            await conn.copy_records_to_table(
                "skills", records=org.skill_rows, columns=["dis_id", "skill", "level"],
            )
            await conn.copy_records_to_table(
                "presence", records=presence,
                columns=["dis_id", "status", "last_seen_at", "source"],
            )
            await conn.execute("ANALYZE employees, reporting_closure, skills, presence")
    finally:
        await conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("-n", "--employees", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=42, help="RNG seed (deterministic output)")
    ap.add_argument("--truncate", action="store_true", help="wipe existing rows first")
    ap.add_argument("--dry-run", action="store_true", help="generate only, no DB writes")
    args = ap.parse_args()

    org = build_org(args.employees, args.seed)
    closure = build_closure(org.parent)
    presence = build_presence(org)

    print(f"employees:         {len(org.rows):>9,}")
    print(f"reporting_closure: {len(closure):>9,}")
    print(f"skills:            {len(org.skill_rows):>9,}")
    print(f"presence:          {len(presence):>9,}")
    print("max depth:        ", max(d for _, _, d in closure))
    print("teams:            ", dict(Counter(r[4] for r in org.rows).most_common(5)), "...")

    if args.dry_run:
        return
    asyncio.run(load(org, closure, presence, args.truncate))
    print("✅ Database seeded successfully")


if __name__ == "__main__":
    main()
