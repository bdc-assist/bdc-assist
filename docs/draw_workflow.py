"""Redraw the workflow pictures from the code, and their mermaid source, renderable, in docs/workflow.md:
uv run python docs/draw_workflow.py

- workflow_general.png (+ _detailed, main_agent's inside too): general purpose, every slot with a
  placeholder node (<slot>_nodes) and the optional built-in steps on; the detailed one has the template
  config's MCP servers and a placeholder for more.
- workflow_bdc.png (+ _detailed): the BDC config (examples/bdc, unless CONFIG_DIR says otherwise),
  its own nodes and MCP servers.

Pastel red: main_agent. Pastel green: what the config alone defines, its own nodes and MCP servers
(none in workflow_bdc.png).

mermaid.ink renders the pictures: the step and server names leave this machine.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # r_assist, when run as a script from anywhere
os.environ.setdefault("CONFIG_DIR", str(ROOT / "examples" / "bdc"))  # before r_assist reads it

from deepagents import create_deep_agent  # noqa: E402
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.runnables.graph_mermaid import _to_safe_id, draw_mermaid_png  # noqa: E402
from langchain_core.tools import StructuredTool  # noqa: E402

from r_assist import prompts  # noqa: E402
from r_assist.agent import _servers_yaml  # noqa: E402
from r_assist.graph import SLOTS, build_graph, drawing, load_nodes  # noqa: E402

# structure only, nothing runs: a fake model
agent = create_deep_agent(GenericFakeChatModel(messages=iter([])), [], system_prompt="")


def bdc():
    """The config's graph, with placeholders for the tools its nodes name in their settings ("tool",
    e.g. related_work), so those steps show; its MCP servers; and its nodes' names."""
    nodes = load_nodes()
    tools = [StructuredTool.from_function(lambda keywords: "", name=s["tool"], description="placeholder")
             for entries in nodes.values() for _, _, s in entries if s.get("tool")]
    return (build_graph(None, agent, prompts.load_predefined_responses(), tools=tools, nodes=nodes),
            _servers_yaml(None), names(nodes))


def general():
    """Every slot with a placeholder node of a config's own, the optional steps on; the template
    config's MCP servers and a placeholder for more; the placeholders' names."""
    def placeholder(llm, tools, settings):
        async def node(state):
            return {}
        return node

    prompts.FOLLOWUPS, prompts.APPEND_DISCLAIMER = max(prompts.FOLLOWUPS, 1), True
    nodes = {slot: [(f"{slot}_nodes", placeholder, {})] for slot in SLOTS}
    servers = {**_servers_yaml(ROOT / "config"), "more_mcp_servers": {"interceptors": ["their_interceptors"]}}
    return build_graph(None, agent, {}, nodes=nodes), servers, names(nodes)


def names(nodes):
    return [name for entries in nodes.values() for name, _, _ in entries]


def own_style(ids):
    """Mermaid lines colouring the ids (the config's alone) pastel green."""
    return f"\tclassDef own fill:#bbf7d0,stroke:#4ade80\n\tclass {','.join(map(_to_safe_id, ids))} own\n" if ids else ""


# pastel red main_agent (a step in one picture, the box in the other): LangChain's NodeStyles only
# colours every step, __start__ and __end__, so a mermaid class line does it (a style line would add a
# main_agent node, which flips the box's layout: tools above model)
STYLE = "\tclassDef agent fill:#fecaca,stroke:#f87171\n\tclass main_agent agent\n"
md = ["# r-assist workflow", "",
      "The mermaid source of the workflow pictures. Written by `docs/draw_workflow.py`: edit the code",
      "and rerun it, not this file."]
for name, title, (graph, servers, own) in [("workflow_bdc", "The BDC config", bdc()),  # before general() turns steps on
                                      ("workflow_general", "General purpose, every slot with a placeholder", general())]:
    for picture, part, mermaid, ids in [
            (name, "", graph.get_graph().draw_mermaid(), [] if name == "workflow_bdc" else own),
            (f"{name}_detailed", ", inside main_agent too", drawing(graph, agent, servers),
             own + [f"main_agent:{s}" for s in servers])]:  # drawing's ids for the servers
        mermaid += STYLE + own_style(ids)
        Path(__file__).with_name(f"{picture}.png").write_bytes(draw_mermaid_png(mermaid))
        md += ["", f"## {title}{part} ({picture}.png)", "", "```mermaid", mermaid.rstrip("\n"), "```"]
        print("wrote", Path(__file__).with_name(f"{picture}.png"))
Path(__file__).with_name("workflow.md").write_text("\n".join(md) + "\n", encoding="utf-8")
print("wrote", Path(__file__).with_name("workflow.md"))
