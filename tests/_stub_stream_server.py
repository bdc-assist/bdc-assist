"""Throwaway stub for eyeballing tests/ui/demo.html, tests/ui/kg_demo.html or the web UI (web/)
without real services: serves the real api app on :8011 with a fake slow agent. Run:
uv run python tests/_stub_stream_server.py, then open tests/ui/demo.html?api=http://127.0.0.1:8011
(or kg_demo.html?api=...), or VITE_API_URL=http://127.0.0.1:8011 npm --prefix web run dev.

By default: a docs answer with a small knowledge graph and the covid disclaimer appended. A keyword
anywhere in the question picks another path:
  block      input guardrail refuses: canned REFUSAL, blocked
  reject     output guardrail rejects the streamed draft: canned REJECT, blocked
  canned     an "r" topic matches: its canned reply, agent skipped
  nosources  the agent answers without searching: no sources, no graph
  crash      the agent fails mid-answer: the stream ends with {"type": "error"}
  kg         the graph is a real Dug get_concept_graph result (congenital heart disease,
             tests/fixtures/dug_concept_graph_chd.json) instead of the small one
  kg2        the same for two concepts, asthma and COPD (dug_concept_graph_asthma_copd.json)
  related    asthma's concept graph plus its related concepts, a real Dug get_concept_connections
             result (dug_concept_connections_asthma.json): concept–concept edges
kg/kg2 graphs are made the way the Dug interceptor makes them (to_kg in examples/bdc/interceptors.py).
"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # runnable from anywhere

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

import r_assist.api as api
from r_assist import prompts
from r_assist.graph import build_graph


def _has(text: str, word: str) -> bool:
    return word in text.lower()


# the Dug interceptor's to_kg: what it attaches to a real get_concept_graph result
_spec = importlib.util.spec_from_file_location("interceptors", Path(__file__).parent.parent / "examples/bdc/interceptors.py")
_interceptors = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_interceptors)
_FIXTURES = Path(__file__).parent / "fixtures"


def _concept_graph(args: dict, result: dict) -> dict:
    return {"tool": "get_concept_graph", "args": args, **_interceptors.to_kg("get_concept_graph", result)}


KG_CHD = [_concept_graph({"concept_id": "MONDO:0005453", "expand_depth": 2, "limit": 50},
                         json.loads((_FIXTURES / "dug_concept_graph_chd.json").read_text()))]
KG_ASTHMA_COPD = [_concept_graph(c["args"], c["result"])
                  for c in json.loads((_FIXTURES / "dug_concept_graph_asthma_copd.json").read_text())]
_connections = json.loads((_FIXTURES / "dug_concept_connections_asthma.json").read_text())
KG_RELATED = [KG_ASTHMA_COPD[0], {"tool": "get_concept_connections", "args": _connections["args"],
                                  **_interceptors.to_kg("get_concept_connections", _connections["result"])}]


class SlowAgent:
    async def astream(self, payload, stream_mode=None):
        question = payload["messages"][0]["content"]
        if _has(question, "nosources"):
            async for item in self._answer_without_search():
                yield item
            return
        yield "messages", (AIMessageChunk(
            content="Let me look that up. ", id="m1",
            tool_call_chunks=[{"name": "search_docs", "args": "", "id": "t1", "index": 0}]), {})
        await asyncio.sleep(1.5)
        words = ("This project's **documentation chatbot** answers questions about the configured docs. "
                 "See [getting started](https://example.org/docs/start).").split()
        for i, w in enumerate(words):
            await asyncio.sleep(0.12)
            if i == 8 and _has(question, "crash"):
                raise RuntimeError("stub: agent crashed mid-answer")
            yield "messages", (AIMessageChunk(content=w + " ", id="m2"), {})
        # final state like the real agent: the tool call, its doc chunks, the answer
        chunks = [
            {"content": "Start here ...", "score": 0.7, "metadata": {"page_url": "https://example.org/docs/start",
                                                                      "doc_type": "docs", "hierarchy": "Getting started, Install"}},
            {"content": "Access ...", "score": 0.8, "metadata": {"page_url": "https://example.org/faq/access",
                                                                  "doc_type": "faq", "title": "How do I get access?"}},
            {"content": "Start here ... (again)", "score": 0.9, "metadata": {"page_url": "https://example.org/docs/start",
                                                                              "doc_type": "docs", "hierarchy": "Getting started"}},
        ]
        # a knowledge graph as an interceptor attaches it (examples/bdc/interceptors.py), for kg_demo.html
        kg = {"tool": "get_concept_graph", "args": {"concept_id": "MONDO:0005068"},
              "nodes": [{"id": "MONDO:0005068", "name": "myocardial infarction", "type": "concept",
                         "category": "biolink:Disease"},
                        {"id": "phv1", "name": "MI_EVER", "type": "variable",
                         "description": "Ever told by a doctor you had a heart attack?"},
                        {"id": "phv2", "name": "MI_AGE", "type": "variable"},
                        {"id": "phv3", "name": "ECG_MI", "type": "variable"},
                        {"id": "phs000007", "name": "Framingham Cohort", "type": "study"},
                        {"id": "phs000280", "name": "Atherosclerosis Risk in Communities (ARIC) Cohort", "type": "study"}],
              "edges": [{"subject": v, "object": "MONDO:0005068", "predicate": "related_to"} for v in ("phv1", "phv2", "phv3")]
                       + [{"subject": "phv1", "object": "phs000007"}, {"subject": "phv2", "object": "phs000007"},
                          {"subject": "phv3", "object": "phs000280"}]}
        kgs = (KG_RELATED if _has(question, "related") else KG_ASTHMA_COPD if _has(question, "kg2")
               else KG_CHD if _has(question, "kg") else [kg])
        yield "values", {"messages": [
            AIMessage(content="", tool_calls=[{"name": "search_docs", "args": {"query": question}, "id": "t1"}]
                      + [{"name": g["tool"], "args": g["args"], "id": f"t{i + 2}"} for i, g in enumerate(kgs)]),
            ToolMessage(content=[{"type": "text", "text": json.dumps(c)} for c in chunks], tool_call_id="t1"),
            *[ToolMessage(content="{}", artifact={"structured_content": {"kg": g}}, tool_call_id=f"t{i + 2}")
              for i, g in enumerate(kgs)],
            AIMessage(content=" ".join(words)),
        ]}

    async def _answer_without_search(self):
        words = "This needs no documents: the answer is general knowledge.".split()
        for w in words:
            await asyncio.sleep(0.12)
            yield "messages", (AIMessageChunk(content=w + " ", id="m1"), {})
        yield "values", {"messages": [AIMessage(content=" ".join(words))]}


TOPICS = {
    "covid": {"response": "Covid disclaimer.", "flag": "a"},
    "canned": {"response": "A canned reply for a predefined topic; the agent didn't run.", "flag": "r"},
}


class ScriptedLLM:
    """Answers by which prompt it receives, not call order. (A response-list fake
    goes out of phase on turn 2: contextualize starts consuming responses once
    chat history exists, so the output guardrail reads the followups list as its
    yes/no verdict and rejects every follow-up.)"""

    _CLASSIFIER = prompts.topic_classifier_system(list(TOPICS))

    async def ainvoke(self, messages):
        role, text = messages[0]
        question = messages[-1][1]  # the current question is in the last message
        if text == prompts.INPUT_GUARDRAIL_SYSTEM:
            # the template itself may say "block", so look only at the filled-in input
            before, after = prompts.INPUT_GUARDRAIL_HUMAN.split("{input}")
            user_input = question[len(before):len(question) - len(after)]
            reply = "Yes" if _has(user_input, "block") else "No"
        elif text == prompts.CONTEXTUALIZE_SYSTEM:
            reply = question  # echo the question unchanged
        elif text == self._CLASSIFIER:
            # canned when asked for, else covid (the disclaimer shows by default)
            reply = "- canned" if _has(question, "canned") else "- covid"
        elif text.startswith(prompts.OUTPUT_GUARDRAIL_HUMAN.split("{")[0]):
            await asyncio.sleep(2)  # slow on purpose: shows the answer + sources rendered before "done"
            reply = "No" if _has(text, "reject") else "Yes"
        elif text.startswith(prompts.SUGGEST_FOLLOWUPS_HUMAN.split("{")[0]):
            await asyncio.sleep(2)
            reply = "- What is dbGaP?\n- How do I get access?"
        else:
            raise AssertionError(f"unexpected prompt: {text[:80]}")
        return AIMessage(content=reply)


api.graph = build_graph(ScriptedLLM(), SlowAgent(), TOPICS)

if __name__ == "__main__":
    from contextlib import asynccontextmanager

    import uvicorn

    @asynccontextmanager
    async def noop_lifespan(app):  # skip the real lifespan (needs MCP); graph already set
        yield
    api.app.router.lifespan_context = noop_lifespan
    uvicorn.run(api.app, port=8011)
