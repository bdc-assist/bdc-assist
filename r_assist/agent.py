import asyncio
import importlib.util
import json
import logging
from pathlib import Path

import yaml
from deepagents import create_deep_agent
from langchain_core.tools import StructuredTool, ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient

from . import prompts

log = logging.getLogger(__name__)
# set when a tool call fails twice (its server may have gone down): api.retry_mcp re-checks now
recheck = asyncio.Event()


def _root(e: BaseException) -> BaseException:
    """The real failure: the adapter wraps transport errors (403, refused) in ExceptionGroups."""
    while isinstance(e, BaseExceptionGroup):
        e = e.exceptions[0]
    return e


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
    raised or returned, is logged with the tool name and arguments. A second transport
    failure (e.g. the server went down mid-session) is handed to the agent as an error
    result instead of crashing the whole run, and sets recheck. A ToolException is the
    server answering with an error: re-raised for the adapter to report, as before."""
    inner = tool.coroutine

    async def retrying(**kwargs):
        for attempt in (1, 2):
            try:
                result = await inner(**kwargs)
            except Exception as e:
                e = _root(e)
                log.warning("%s%s raised %s: %s (attempt %d)", tool.name, kwargs, type(e).__name__, e, attempt)
                if attempt == 2:
                    if isinstance(e, ToolException):
                        raise
                    recheck.set()
                    msg = f"Error: {tool.name} failed ({type(e).__name__}: {e}); its server may be down."
                    return (msg, None) if tool.response_format == "content_and_artifact" else msg
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
    `interceptors:` and `stand_ins:` are r-assist's (see load_interceptors, _stand_in), not
    connection settings, so they are dropped here: the adapter hands every other key to the transport."""
    return {name: {k: v for k, v in conn.items() if k not in ("interceptors", "stand_ins")}
            for name, conn in _servers_yaml(data_dir).items()}


def _stand_in(name: str, server: str):
    """A tool of a server that is down (`stand_ins:` in mcp_servers.yaml), answering with an error.
    The agent prompt names that server's tools; with them simply missing, the agent answers from
    the tools left (tested: release-note study lists passed off as catalog results), and a note in
    the system prompt did not stop it. Calling a stand-in returns an error, so the prompt's own
    "if a tool returns an error" rule applies. Takes any arguments: the real schema is unknown."""
    async def unavailable(**kwargs):
        return (f"Error: the {server} service is unavailable right now. Tell the user; do not fill in "
                "its part of the answer from other tools.")
    return StructuredTool.from_function(coroutine=unavailable, name=name, description=f"{name} ({server})",
                                        args_schema={"type": "object", "properties": {}, "additionalProperties": True})


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
    """-> (agent, errors). Each server's tools load on their own: an unreachable server (e.g.
    dug_mcp off the RENCI VPN: 403) is logged and skipped, one "name: error" line in errors,
    instead of failing startup; its `stand_ins:` take its tools' place. api.retry_mcp rebuilds
    once it is back."""
    stand_ins = {name: conn.get("stand_ins") or [] for name, conn in _servers_yaml(None).items()}
    client = MultiServerMCPClient(load_mcp_servers(), tool_interceptors=load_interceptors())
    names = list(client.connections)
    results = await asyncio.gather(*(client.get_tools(server_name=n) for n in names), return_exceptions=True)
    tools, errors = [], []
    for name, result in zip(names, results):
        if not isinstance(result, BaseException):
            tools += [_with_retry(t) for t in result]
            continue
        result = _root(result)
        errors.append(f"{name}: {type(result).__name__}: {str(result).split(chr(10))[0]}")
        log.warning("MCP server unavailable, running without its tools: %s", errors[-1])
        tools += [_stand_in(t, name) for t in stand_ins[name]]
    return create_deep_agent(llm, tools, system_prompt=prompts.agent_system()), errors
