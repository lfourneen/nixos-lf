import asyncio
import json
import os
from pathlib import Path
import signal
import ssl
import sys
from urllib.request import urlopen

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import yaml


keys, gateway_env, backend_env = json.loads(Path(sys.argv[1]).read_text())
gateway = sys.argv[3] == "gateway"
expected = gateway_env if gateway else backend_env
assert all(os.environ.get(key) == expected.get(key) for key in keys)
assert os.environ.get("MANAGER_PROBE") == "present"
assert os.environ.get("FILE_PROBE") == "present"
assert ssl.create_default_context().cert_store_stats()["x509_ca"] > 0
if gateway:
    assert "DEEPSEEK_API_KEY" in os.environ
    assert ("HTTP_PROXY" in expected) == (sys.argv[2] == "compat")
    if "HTTP_PROXY" in expected:
        with urlopen("http://gost-fixture.invalid/", timeout=5) as response:
            assert response.read() == b"approved-proxy"


async def use_time():
    config_path = Path(os.environ["HERMES_HOME"]) / "config.yaml"
    server = yaml.safe_load(config_path.read_text())["mcp_servers"]["time"]
    # Feed the actual stdio command a deliberately contaminated per-tool env.
    env = {key: "fixture" for key in keys + ["DEEPSEEK_API_KEY"]}
    params = StdioServerParameters(command=server["command"], args=server["args"], env=env)
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert "Pacific/Auckland" in tools["get_current_time"].inputSchema["properties"]["timezone"]["description"]
            result = await session.call_tool("get_current_time", {"timezone": "UTC"})
            assert not result.isError
            assert json.loads(result.content[0].text)["timezone"] == "UTC"
            result = await session.call_tool("convert_time", {
                "source_timezone": "UTC", "time": "12:00", "target_timezone": "Asia/Taipei",
            })
            assert not result.isError
            assert json.loads(result.content[0].text)["target"]["datetime"][11:16] == "20:00"
            Path("time-mcp-ready").touch()
            await asyncio.Event().wait()


if gateway:
    asyncio.run(use_time())
else:
    Path("backend-env-ready").touch()
    signal.pause()
