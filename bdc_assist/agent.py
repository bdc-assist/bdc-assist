import asyncio
from pathlib import Path

import yaml
from deepagents import create_deep_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

from . import prompts

_SERVERS = Path(__file__).resolve().parent.parent / "data" / "mcp_servers.yaml"


def _with_retry(tool):
    """One retry on any failure: search is idempotent, and each call opens a fresh
    MCP session, so a dropped connection heals on the second attempt."""
    inner = tool.coroutine

    async def retrying(**kwargs):
        try:
            return await inner(**kwargs)
        except Exception:
            await asyncio.sleep(1)
            return await inner(**kwargs)

    tool.coroutine = retrying
    return tool


def load_mcp_servers() -> dict:
    with open(_SERVERS, encoding="utf-8") as f:
        return yaml.safe_load(f)


async def build_agent(llm):
    client = MultiServerMCPClient(load_mcp_servers())
    tools = [_with_retry(t) for t in await client.get_tools()]
    return create_deep_agent(llm, tools, system_prompt=prompts.agent_system())
