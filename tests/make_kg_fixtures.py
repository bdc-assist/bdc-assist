"""Writes tests/fixtures/kg/<keyword>.json, one per stub graph keyword: the knowledge graphs and
Dug sources the server sends (the done event's "kg" and sources["dug"]) for real Dug results in
tests/fixtures/, as the Dug interceptor attaches them. The stub serves these files, and
bdc-assist-client copies them for its tests; test_kg_fixtures fails while they're out of date.
After changing the interceptor or a Dug fixture, run: uv run python tests/make_kg_fixtures.py
"""

import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # runnable from anywhere

from langchain_core.messages import ToolMessage

from r_assist.graph import _attached_sources, _kgs

FIXTURES = Path(__file__).parent / "fixtures"
OUT = FIXTURES / "kg"

_spec = importlib.util.spec_from_file_location("interceptors", Path(__file__).parent.parent / "examples/bdc/interceptors.py")
_interceptors = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_interceptors)

# keyword (the Dug tool, minus get_) -> (tool, Dug fixture). Fixtures hold {args, result} or a list
# of them; dug_concept_graph_chd.json is a bare result, so its args are given here.
CHD_ARGS = {"concept_id": "MONDO:0005453", "expand_depth": 2, "limit": 50}
KEYWORDS = {
    "concept_graph": ("get_concept_graph", "dug_concept_graph_chd.json"),
    "concept_graph_2": ("get_concept_graph", "dug_concept_graph_asthma_copd.json"),
    "concept_connections": ("get_concept_connections", "dug_concept_connections_asthma.json"),
    "cohort_variables": ("find_cohort_variables", "dug_find_cohort_variables_asthma_copd.json"),
    "search_concepts": ("search_concepts", "dug_search_concepts_bmi.json"),
}


def _calls(name: str) -> list[dict]:
    saved = json.loads((FIXTURES / name).read_text())
    if isinstance(saved, dict) and "result" not in saved:
        return [{"args": CHD_ARGS, "result": saved}]
    return saved if isinstance(saved, list) else [saved]


def _attached(tool: str, args: dict, result: dict) -> dict:
    """The structured content dug_kg attaches to one call: its kg, and the studies Dug cites."""
    attached = {"kg": _interceptors.entry(tool, args, result, _interceptors.to_kg(tool, result))}
    if result.get("_sources"):
        attached["sources"] = {"dug": result["_sources"]}
    return attached


def build(keyword: str) -> dict:
    """{"kg": [...], "sources": {"dug": [...]}} as the done event carries them for this keyword."""
    tool, name = KEYWORDS[keyword]
    messages = [ToolMessage(content="{}", artifact={"structured_content": _attached(tool, c["args"], c["result"])},
                            tool_call_id=f"t{i}") for i, c in enumerate(_calls(name))]
    return {"kg": _kgs(messages), "sources": _attached_sources(messages)}


def text(keyword: str) -> str:
    return json.dumps(build(keyword), indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for keyword in KEYWORDS:
        (OUT / f"{keyword}.json").write_text(text(keyword), encoding="utf-8")
        print(OUT / f"{keyword}.json")
