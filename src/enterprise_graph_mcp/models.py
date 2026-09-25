"""Pydantic models for structured MCP tool output."""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class PeerMatch(BaseModel):
    """A ranked peer candidate for cross-functional collaboration."""
    dis_id: str
    display_name: str
    team_id: str
    timezone: str
    local_time: str = Field(description="Current local time in ISO 8601 with offset")
    skill_level: int = Field(ge=1, le=5, description="Proficiency level for the queried skill")
    timezone_overlap_hours: float = Field(ge=0, le=24, description="Working hours overlapping with requester")
    cross_team: bool = Field(description="Whether candidate is in a different team")
    reason: str = Field(description="Human-readable rationale for the match")


class BuddyPairing(BaseModel):
    """A new hire paired with an onboarding buddy."""
    new_hire_id: str
    new_hire_name: str
    buddy_id: str
    buddy_name: str
    buddy_team_id: str
    match_score: float = Field(ge=0, le=1)
    shared_skills: list[str] = Field(default_factory=list)
    seat_proximity: Optional[str] = Field(
        default=None,
        description="e.g., 'same_floor', 'same_building', 'remote'"
    )
    reason: str


class TeamTopology(BaseModel):
    """Organization topology for a team."""
    team_id: str
    team_name: str
    members: list[dict] = Field(description="Employee records with reporting edges")
    cross_team_edges: list[dict] = Field(
        default_factory=list,
        description="Dotted-line reports, guild memberships, project assignments"
    )


class PresenceInfo(BaseModel):
    """Live presence and availability for an employee."""
    dis_id: str
    display_name: str
    status: str = Field(description="active | away | dnd | offline")
    local_time: str = Field(description="Current local time, ISO 8601 with offset")
    timezone: str
    working_hours: str = Field(description="e.g., '09:00-17:30'")
    next_meeting: Optional[datetime] = None