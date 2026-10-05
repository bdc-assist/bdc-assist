import asyncio
import importlib.util
import json
import logging
from pathlib import Path

import yaml
from deepagents import create_deep_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

from . import prompts

log = logging.getLogger(__name__)


def _error_text(result):
    """Some servers report failures as a normal result: {"error": "..."} or a "warnings": [...]
    list next to empty data. Nothing would ever log those. Return the message(s), or None."""
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


def _servers_yaml(data_dir: Path | None) -> dict:
    with open((data_dir or prompts.DATA_DIR) / "mcp_servers.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_mcp_servers(data_dir: Path | None = None) -> dict:
    """<config dir>/mcp_servers.yaml -> the MultiServerMCPClient connections dict. Same folder as
    prompts.yaml (CONFIG_DIR), so examples/bdc carries its own server list. A server's optional
    `interceptors:` is r-assist's (see load_interceptors), not a connection setting, so it is
    dropped here: the adapter hands every other key to the transport."""
    return {name: {k: v for k, v in conn.items() if k != "interceptors"}
            for name, conn in _servers_yaml(data_dir).items()}


def _only_for(server: str, interceptor):
    async def scoped(request, handler):
        if request.server_name != server:
            return await handler(request)
        return await interceptor(request, handler)
    return scoped


def load_interceptors(data_dir: Path | None = None) -> list:
    """Each server's `interceptors: [name, ...]` in mcp_servers.yaml, looked up in
    <config dir>/interceptors.py and run on that server's tool calls only. Lets a deployment
    reshape a server's results (e.g. examples/bdc attaches a knowledge graph to dug_mcp's) with
    no change here. Each is a langchain-mcp-adapters tool interceptor, async (request, handler);
    a server's list runs outermost first."""
    data_dir = data_dir or prompts.DATA_DIR
    wanted = {name: conn["interceptors"] for name, conn in _servers_yaml(data_dir).items()
              if conn.get("interceptors")}
    if not wanted:
        return []
    path = data_dir / "interceptors.py"
    spec = importlib.util.spec_from_file_location("config_interceptors", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # a missing file fails startup here
    scoped = []
    for server, names in wanted.items():
        for name in names:
            if not callable(getattr(module, name, None)):
                raise ValueError(f"mcp_servers.yaml [{server}]: no function {name} in {path}")
            scoped.append(_only_for(server, getattr(module, name)))
    return scoped


async def build_agent(llm):
    client = MultiServerMCPClient(load_mcp_servers(), tool_interceptors=load_interceptors())
    tools = [_with_retry(t) for t in await client.get_tools()]
    return create_deep_agent(llm, tools, system_prompt=prompts.agent_system())
