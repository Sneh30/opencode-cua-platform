import asyncio
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SERVER = StdioServerParameters(
    command="uv",
    args=["run", "--project", "gateway", "cua-gateway"],
    cwd=str(REPO_ROOT),
)


async def _conversation():
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            act = await session.call_tool(
                "gui.act",
                {
                    "session_id": "s1",
                    "action": {"type": "click", "target_hint": "OK button"},
                    "expected_postcondition": "dialog closed",
                },
            )
            missing = await session.call_tool("gui.act", {"session_id": "s1"})
            verify = await session.call_tool(
                "gui.verify", {"session_id": "s1", "action_id": "stub-abc"}
            )
            return init, tools, act, missing, verify


def _text(result) -> str:
    return result.content[0].text


def test_gateway_stub_smoke():
    init, tools, act, missing, verify = asyncio.run(_conversation())

    assert init.serverInfo.name == "cua-gateway"
    assert init.instructions, "MCP instructions field must be non-empty"
    assert init.instructions.startswith("# Role")

    names = [t.name for t in tools.tools]
    assert names == ["gui.observe", "gui.act", "gui.verify"]

    act_payload = json.loads(_text(act))
    assert act_payload["dispatched"] is False
    assert act_payload["stub"] is True
    assert act_payload["action_id"].startswith("stub-")

    assert missing.isError, "gui.act without expected_postcondition must be rejected"

    verify_payload = json.loads(_text(verify))
    assert verify_payload["verdict"] == "uncertain"


def test_master_prompt_placeholder_marker_present():
    import mcp_server

    assert "M0P-7F3A9C" in mcp_server.MASTER_PROMPT