"""End to end: Claude Code's view. Starts the real MCP stdio server (simulator mode), which in turn
spawns the background service process, then calls tools through the official MCP client."""
import asyncio
import os
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conftest import free_port
from nanokvm_mcp.client import ServiceClient
from nanokvm_mcp.config import Config


def test_mcp_end_to_end(tmp_path):
    port = free_port()
    env = dict(os.environ, NANOKVM_SIMULATE="1", NANOKVM_HOME=str(tmp_path), NANOKVM_PORT=str(port),
               NANOKVM_IDLE_EXIT_MIN="2", NANOKVM_KEY_HOLD_MS="2", NANOKVM_KEY_GAP_MS="2")
    params = StdioServerParameters(command=sys.executable, args=["-m", "nanokvm_mcp.mcp_server"], env=env)

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                assert {"screenshot", "zoom", "wait_for_change", "screen_status", "click", "type_text",
                        "press_keys", "release_all", "stop_service", "start_screen_sharing",
                        "stop_screen_sharing"} <= names
                tools = {t.name: t for t in (await session.list_tools()).tools}
                assert (tools["start_screen_sharing"].meta or {}).get("anthropic/requiresUserInteraction") is True

                refused = await session.call_tool("screenshot", {})          # not shared yet
                assert refused.is_error and "sharing is off" in refused.content[0].text
                started = await session.call_tool("start_screen_sharing", {"minutes": 5})
                assert not started.is_error and "ON" in started.content[0].text

                shot = await session.call_tool("screenshot", {})
                assert not shot.is_error, shot
                kinds = [c.type for c in shot.content]
                assert kinds == ["text", "image"]
                assert shot.content[1].mime_type == "image/jpeg"
                assert "1280x720" in shot.content[0].text

                typed = await session.call_tool("type_text", {"text": "hi from claude"})
                assert not typed.is_error, typed
                assert typed.content[0].text.startswith("Done: typed 14")
                assert typed.content[-1].type == "image"

                bad = await session.call_tool("click", {"x": 99999, "y": 5})
                assert bad.is_error and "outside the screen" in bad.content[0].text

                st = await session.call_tool("screen_status", {})
                text = st.content[0].text
                assert "SIMULATOR" in text and "Live view" in text and "connected" in text

                assert "Screen sharing with this conversation: ON" in text
                await session.call_tool("stop_screen_sharing", {})
                again = await session.call_tool("zoom", {"x": 0, "y": 0, "width": 100, "height": 100})
                assert again.is_error and "sharing is off" in again.content[0].text
                no_shot = await session.call_tool("press_keys", {"keys": "a"})   # actions still work...
                assert not no_shot.is_error
                assert [c.type for c in no_shot.content] == ["text"]             # ...but return no image
                assert "sharing is off" in no_shot.content[0].text

                stopped = await session.call_tool("stop_service", {})
                assert "stopped" in stopped.content[0].text

    try:
        asyncio.run(asyncio.wait_for(run(), 120))
    finally:
        ServiceClient(Config.load(env=env)).stop()


def test_cancelled_typing_stops_on_the_target(tmp_path):
    """Pressing Esc in Claude Code cancels the MCP request; the server must stop the typing."""
    port = free_port()
    env = dict(os.environ, NANOKVM_SIMULATE="1", NANOKVM_HOME=str(tmp_path), NANOKVM_PORT=str(port),
               NANOKVM_IDLE_EXIT_MIN="2", NANOKVM_KEY_HOLD_MS="10", NANOKVM_KEY_GAP_MS="10")
    params = StdioServerParameters(command=sys.executable, args=["-m", "nanokvm_mcp.mcp_server"], env=env)
    svc = ServiceClient(Config.load(env=env))

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool("screen_status", {})         # starts the service
                with anyio.move_on_after(0.8):
                    await session.call_tool("type_text", {"text": "z" * 300, "screenshot_after": False})
                await anyio.sleep(1.5)
                text = svc.status()["sim"]["text"]
                assert 0 < len(text) < 300, len(text)

    try:
        anyio.run(run)
    finally:
        svc.stop()


def _list_tools(env):
    params = StdioServerParameters(command=sys.executable, args=["-m", "nanokvm_mcp.mcp_server"], env=env)

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return {t.name: t for t in (await session.list_tools()).tools}

    return asyncio.run(asyncio.wait_for(run(), 60))


def test_confirm_actions_marks_control_tools(tmp_path):
    env = dict(os.environ, NANOKVM_HOME=str(tmp_path), NANOKVM_CONFIRM_ACTIONS="true")
    tools = _list_tools(env)
    assert (tools["click"].meta or {}).get("anthropic/requiresUserInteraction") is True
    assert not (tools["screenshot"].meta or {}).get("anthropic/requiresUserInteraction")


def test_view_only_hides_control_tools(tmp_path):
    env = dict(os.environ, NANOKVM_HOME=str(tmp_path), NANOKVM_CONTROL="false")
    tools = _list_tools(env)
    assert "screenshot" in tools and "wait_for_change" in tools
    assert not {"click", "type_text", "press_keys", "drag", "scroll"} & set(tools)
