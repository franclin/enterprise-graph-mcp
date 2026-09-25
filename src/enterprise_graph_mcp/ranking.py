"""Ranking and matching algorithms for peer discovery and buddy pairing."""
from typing import Optional

from enterprise_graph_mcp.models import PeerMatch, BuddyPairing


def rank_peers(
    candidates: list[dict],
    target_skill: str,
    target_timezone: str,
    requester_team: Optional[str] = None,
) -> list[PeerMatch]:
    """
    Rank peer candidates by composite score.

    Weights:
      - Skill proficiency: 50%
      - Timezone overlap: 30%
      - Cross-team bonus: 20%
    """
    results = []
    for c in candidates:
        # Normalize skill level (1-5) to 0-1
        skill_weight = c.get("skill_level", 1) / 5.0

        # Timezone overlap (already computed or default)
        tz_overlap = c.get("timezone_overlap_hours", 4.0)
        tz_weight = min(tz_overlap / 8.0, 1.0)

        # Cross-team bonus
        cross_team = c.get("team_id") != requester_team if requester_team else True
        cross_weight = 1.0 if cross_team else 0.0

        score = (skill_weight * 0.5) + (tz_weight * 0.3) + (cross_weight * 0.2)

        results.append(PeerMatch(
            dis_id=c["dis_id"],
            display_name=c["display_name"],
            team_id=c["team_id"],
            timezone=c["timezone"],
            local_time=c.get("local_time", c["timezone"]),
            skill_level=c.get("skill_level", 1),
            timezone_overlap_hours=tz_overlap,
            cross_team=cross_team,
            reason=(
                f"Skill {c.get('skill_level', '?')}/5; "
                f"{tz_overlap:.1f}h overlap; "
                f"{'cross-team' if cross_team else 'same team'}; "
                f"composite {score:.2f}"
            ),
        ))

    results.sort(key=lambda p: p.skill_level, reverse=True)
    return results


def generate_buddy_pairings(
    new_hire: dict,
    new_hire_skills: list[str],
    candidates: list[dict],
    cohort_size: int = 1,
) -> list[dict]:
    """
    Generate buddy pairings for a new hire.

    Scores candidates by:
      - Shared skills (40%)
      - Seat proximity (30%)
      - Team proximity (20%)
      - Tenure (10%)
    """
    scored = []

    for c in candidates:
        # Shared skills (we'd need candidate skills from DB; simplified here)
        shared_skills = []  # Would query candidate skills
        skill_score = len(shared_skills) / max(len(new_hire_skills), 1)

        # Seat proximity
        if c.get("seat_building") == new_hire.get("seat_building"):
            if c.get("seat_floor") == new_hire.get("seat_floor"):
                seat_score = 1.0
                seat_desc = "same_floor"
            else:
                seat_score = 0.6
                seat_desc = "same_building"
        else:
            seat_score = 0.2
            seat_desc = "remote"

        # Team proximity
        team_score = 1.0 if c.get("team_id") == new_hire.get("team_id") else 0.4

        # Tenure (normalize: 6 months = 0.5, 24+ months = 1.0)
        tenure_months = c.get("tenure_months", 6)
        tenure_score = min(tenure_months / 24.0, 1.0)

        total = (
            skill_score * 0.4 +
            seat_score * 0.3 +
            team_score * 0.2 +
            tenure_score * 0.1
        )

        scored.append({
            "buddy_id": c["dis_id"],
            "buddy_name": c["display_name"],
            "buddy_team_id": c["team_id"],
            "score": total,
            "shared_skills": shared_skills,
            "seat_proximity": seat_desc,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    return [
        {
            "new_hire_id": new_hire["dis_id"],
            "new_hire_name": new_hire["display_name"],
            "buddy_id": s["buddy_id"],
            "buddy_name": s["buddy_name"],
            "buddy_team_id": s["buddy_team_id"],
            "match_score": s["score"],
            "shared_skills": s["shared_skills"],
            "seat_proximity": s["seat_proximity"],
            "reason": (
                f"Score {s['score']:.2f}; "
                f"{s['seat_proximity']}; "
                f"{len(s['shared_skills'])} shared skills"
            ),
        }
        for s in scored[:cohort_size]
    ]