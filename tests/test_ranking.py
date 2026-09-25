import pytest
from enterprise_graph_mcp.ranking import rank_peers

def test_rank_peers_prioritizes_skill_level():
    candidates = [
        {"dis_id": "alice", "display_name": "Alice", "team_id": "platform",
         "timezone": "Europe/Dublin", "skill_level": 5, "timezone_overlap_hours": 8.0},
        {"dis_id": "bob", "display_name": "Bob", "team_id": "backend",
         "timezone": "Europe/Dublin", "skill_level": 2, "timezone_overlap_hours": 8.0},
    ]
    results = rank_peers(candidates, "python", "Europe/Dublin")
    assert results[0].dis_id == "alice"  # higher skill ranks first