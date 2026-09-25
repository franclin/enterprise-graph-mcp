import pytest
from mcp.client import ClientSession
from enterprise_graph_mcp.server import mcp

@pytest.mark.asyncio
async def test_list_tools():
    # Connect via in-memory transport
    async with ClientSession(mcp) as session:
        await session.initialize()
        tools = await session.list_tools()
        names = [t.name for t in tools.tools]
        assert "find_cross_functional_peers" in names
        assert "generate_onboarding_buddy_pairings" in names

@pytest.mark.asyncio
async def test_call_tool():
    async with ClientSession(mcp) as session:
        await session.initialize()
        result = await session.call_tool(
            "find_cross_functional_peers",
            {"skill": "python", "timezone": "Europe/Dublin"}
        )
        assert not result.isError