import asyncio

from deepagents import create_deep_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

from . import config, prompts


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


async def build_agent(llm):
    client = MultiServerMCPClient({
        "bdc_doc_mcp": {"transport": "streamable_http", "url": config.DOC_RAG_MCP_URL},
    })
    tools = [_with_retry(t) for t in await client.get_tools()]
    return create_deep_agent(llm, tools, system_prompt=prompts.agent_system())
