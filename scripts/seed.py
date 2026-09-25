"""Seed the database with sample enterprise data."""
import asyncio
import asyncpg

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS employees (
    dis_id          TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    manager_id      TEXT REFERENCES employees(dis_id),
    team_id         TEXT NOT NULL,
    timezone        TEXT NOT NULL,
    seat_building   TEXT,
    seat_floor      INT,
    hired_at        DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS reporting_closure (
    ancestor_id   TEXT,
    descendant_id TEXT,
    depth         SMALLINT,
    PRIMARY KEY (ancestor_id, descendant_id)
);

CREATE TABLE IF NOT EXISTS skills (
    dis_id   TEXT REFERENCES employees(dis_id),
    skill    TEXT NOT NULL,
    level    SMALLINT CHECK (level BETWEEN 1 AND 5),
    PRIMARY KEY (dis_id, skill)
);

CREATE TABLE IF NOT EXISTS presence (
    dis_id       TEXT PRIMARY KEY REFERENCES employees(dis_id),
    status       TEXT NOT NULL DEFAULT 'offline',
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source       TEXT NOT NULL DEFAULT 'slack'
);

-- Indexes for query performance
CREATE INDEX IF NOT EXISTS idx_employees_team ON employees(team_id);
CREATE INDEX IF NOT EXISTS idx_employees_manager ON employees(manager_id);
CREATE INDEX IF NOT EXISTS idx_skills_skill ON skills(skill);
CREATE INDEX IF NOT EXISTS idx_skills_dis_skill ON skills(dis_id, skill);
CREATE INDEX IF NOT EXISTS idx_closure_ancestor ON reporting_closure(ancestor_id, depth);
CREATE INDEX IF NOT EXISTS idx_closure_descendant ON reporting_closure(descendant_id);
"""

SEED_SQL = """
-- Sample teams: platform, frontend, backend, data

INSERT INTO employees (dis_id, display_name, email, manager_id, team_id, timezone, seat_building, seat_floor, hired_at)
VALUES
    -- Platform team
    ('alice', 'Alice Chen', 'alice@corp.com', NULL, 'platform', 'Europe/Dublin', 'HQ', 3, '2020-01-15'),
    ('bob', 'Bob Murphy', 'bob@corp.com', 'alice', 'platform', 'Europe/Dublin', 'HQ', 3, '2021-03-10'),
    ('carol', 'Carol O''Brien', 'carol@corp.com', 'alice', 'platform', 'America/New_York', 'NYC', 2, '2022-06-01'),
    -- Frontend team
    ('dave', 'Dave Kim', 'dave@corp.com', NULL, 'frontend', 'Europe/Dublin', 'HQ', 2, '2019-08-20'),
    ('eve', 'Eve Patel', 'eve@corp.com', 'dave', 'frontend', 'Europe/Dublin', 'HQ', 2, '2023-01-05'),
    -- Backend team
    ('frank', 'Frank Novak', 'frank@corp.com', NULL, 'backend', 'Asia/Tokyo', 'Tokyo', 1, '2018-11-01'),
    ('grace', 'Grace Liu', 'grace@corp.com', 'frank', 'backend', 'Asia/Tokyo', 'Tokyo', 1, '2022-09-15'),
    -- Data team
    ('henry', 'Henry Adeyemi', 'henry@corp.com', NULL, 'data', 'Europe/London', 'HQ', 4, '2020-05-20'),
    ('iris', 'Iris Zhang', 'iris@corp.com', 'henry', 'data', 'Europe/Dublin', 'HQ', 4, '2023-07-01');

-- Reporting closure table (depth 0 = self)
INSERT INTO reporting_closure (ancestor_id, descendant_id, depth)
SELECT dis_id, dis_id, 0 FROM employees;

-- Build hierarchy edges (recursive in a real system; static here)
INSERT INTO reporting_closure (ancestor_id, descendant_id, depth)
VALUES
    ('alice', 'bob', 1), ('alice', 'carol', 1),
    ('dave', 'eve', 1),
    ('frank', 'grace', 1),
    ('henry', 'iris', 1)
ON CONFLICT DO NOTHING;

-- Skills
INSERT INTO skills (dis_id, skill, level) VALUES
    ('alice', 'python', 5), ('alice', 'kubernetes', 5), ('alice', 'postgresql', 4),
    ('bob', 'python', 4), ('bob', 'postgresql', 4),
    ('carol', 'python', 3), ('carol', 'kubernetes', 3),
    ('dave', 'typescript', 5), ('dave', 'react', 5),
    ('eve', 'typescript', 3), ('eve', 'react', 4),
    ('frank', 'rust', 5), ('frank', 'python', 4),
    ('grace', 'rust', 3), ('grace', 'kubernetes', 4),
    ('henry', 'python', 5), ('henry', 'sql', 5),
    ('iris', 'python', 3), ('iris', 'sql', 4);

-- Presence
INSERT INTO presence (dis_id, status, source) VALUES
    ('alice', 'active', 'slack'),
    ('bob', 'active', 'slack'),
    ('carol', 'away', 'webex'),
    ('dave', 'dnd', 'slack'),
    ('eve', 'active', 'slack'),
    ('frank', 'offline', 'slack'),
    ('grace', 'active', 'slack'),
    ('henry', 'active', 'slack'),
    ('iris', 'away', 'webex')
ON CONFLICT (dis_id) DO UPDATE SET
    status = EXCLUDED.status,
    last_seen_at = NOW();
"""


async def seed():
    """Create schema and seed sample data."""
    conn = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost:5432/enterprise_graph"
    )
    try:
        await conn.execute(SCHEMA_SQL)
        await conn.execute(SEED_SQL)
        print("✅ Database seeded successfully")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed())