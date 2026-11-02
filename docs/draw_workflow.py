"""Redraw docs/workflow.png and docs/workflow_detailed.png (main_agent's inside too) from the code:
uv run python docs/draw_workflow.py

Draws the BDC config (examples/bdc, unless CONFIG_DIR says otherwise), so related_work and the BDC
MCP servers show. mermaid.ink renders the picture: the step and server names leave this machine.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # r_assist, when run as a script from anywhere
os.environ.setdefault("CONFIG_DIR", str(ROOT / "examples" / "bdc"))  # before r_assist reads it

from deepagents import create_deep_agent  # noqa: E402
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.runnables.graph_mermaid import draw_mermaid_png  # noqa: E402
from langchain_core.tools import StructuredTool  # noqa: E402

from r_assist import prompts  # noqa: E402
from r_assist.agent import _servers_yaml  # noqa: E402
from r_assist.graph import build_graph, drawing  # noqa: E402

# structure only, nothing runs: a fake model, and a placeholder for the related tool so related_work shows
agent = create_deep_agent(GenericFakeChatModel(messages=iter([])), [], system_prompt="")
related = [StructuredTool.from_function(lambda keywords: "", name=prompts.RELATED_TOOL, description="placeholder")
           ] if prompts.RELATED_TOOL else []
graph = build_graph(None, agent, prompts.load_predefined_responses(), tools=related)
# pastel red main_agent (a step in one picture, the box in the other): LangChain's NodeStyles only
# colours every step, __start__ and __end__, so a mermaid style line does it
STYLE = "\tstyle main_agent fill:#fecaca,stroke:#f87171\n"
for name, mermaid in [("workflow.png", graph.get_graph().draw_mermaid()),
                      ("workflow_detailed.png", drawing(graph, agent, _servers_yaml(None)))]:
    Path(__file__).with_name(name).write_bytes(draw_mermaid_png(mermaid + STYLE))
    print("wrote", Path(__file__).with_name(name))
