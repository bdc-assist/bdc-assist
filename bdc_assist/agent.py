import asyncio
import json
import logging
from pathlib import Path

import yaml
from deepagents import create_deep_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

from . import prompts

log = logging.getLogger(__name__)
_SERVERS = Path(__file__).resolve().parent.parent / "data" / "mcp_servers.yaml"


def _error_text(result):
    """Some servers (dug-mcp) report failures as a normal result: {"error": "..."} or a
    "warnings": [...] list next to empty data. Nothing would ever log those. Return the
    message(s), or None."""
    if isinstance(result, tuple):  # langchain-mcp-adapters returns (content, artifact)
        result = result[0]
    blocks = result if isinstance(result, list) else [result]
    for b in blocks:
        text = b.get("text") if isinstance(b, dict) else b
        if not (isinstance(text, str) and text.lstrip().startswith("{")):
            continue
        if '"error"' not in text and '"warnings"' not in text:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            return text[:300]
        if data.get("error"):
            return data["error"]
        if data.get("warnings"):
            return "; ".join(map(str, data["warnings"]))
    return None


def _with_retry(tool):
    """One retry on any failure: search is idempotent, and each call opens a fresh
    MCP session, so a dropped connection heals on the second attempt. Every failure,
    raised or returned, is logged with the tool name and arguments."""
    inner = tool.coroutine

    async def retrying(**kwargs):
        for attempt in (1, 2):
            try:
                result = await inner(**kwargs)
            except Exception as e:
                log.warning("%s%s raised %s: %s (attempt %d)", tool.name, kwargs, type(e).__name__, e, attempt)
                if attempt == 2:
                    raise
                await asyncio.sleep(1)
                continue
            err = _error_text(result)
            if err:
                log.warning("%s%s returned an error: %s", tool.name, kwargs, err)
            return result

    tool.coroutine = retrying
    return tool


def load_mcp_servers() -> dict:
    with open(_SERVERS, encoding="utf-8") as f:
        return yaml.safe_load(f)


async def build_agent(llm):
    client = MultiServerMCPClient(load_mcp_servers())
    tools = [_with_retry(t) for t in await client.get_tools()]
    return create_deep_agent(llm, tools, system_prompt=prompts.agent_system())
