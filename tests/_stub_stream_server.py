"""Throwaway stub for eyeballing tests/ui/demo.html, tests/ui/kg_demo.html or the web client (bdc-assist-client)
without real services: serves the real api app on :8011 with a fake slow agent. Run:
uv run python tests/_stub_stream_server.py, then open tests/ui/demo.html?api=http://127.0.0.1:8011
(or kg_demo.html?api=...), or VITE_API_URL=http://127.0.0.1:8011 npm run dev in bdc-assist-client.

By default: a docs answer with a small knowledge graph and the covid disclaimer appended. A keyword
(a whole word) anywhere in the question picks another path:
  block      input guardrail refuses: canned REFUSAL, blocked
  reject     output guardrail rejects the streamed draft: canned REJECT, blocked
  canned     an "r" topic matches: its canned reply, agent skipped
  nosources  the agent answers without searching: no sources, no graph
  crash      the agent fails mid-answer: the stream ends with {"type": "error"}
Graph keywords are named after the Dug tool whose real result (tests/fixtures/) they replay, and
combine: each adds its calls, so "concept_graph_2 concept_connections" draws asthma and COPD
plus asthma's related concepts.
  concept_graph        get_concept_graph, congenital heart disease (dug_concept_graph_chd.json)
  concept_graph_2      get_concept_graph twice, asthma and COPD (dug_concept_graph_asthma_copd.json)
  concept_connections  get_concept_connections, asthma (dug_concept_connections_asthma.json):
                       concept–concept edges, no variables or studies
  cohort_variables     find_cohort_variables, asthma + COPD (dug_find_cohort_variables_asthma_copd.json):
                       search-term nodes, no concepts
  search_concepts      search_concepts, body mass index (dug_search_concepts_bmi.json): 10 variables
                       each linked to the same 9 concepts, no studies, no seeds
Their graphs are tests/fixtures/kg/<keyword>.json, made by tests/make_kg_fixtures.py.
"""

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # runnable from anywhere

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

import r_assist.api as api
from r_assist import prompts
from r_assist.graph import build_graph
from tests.make_kg_fixtures import KEYWORDS, OUT


def _has(text: str, word: str) -> bool:
    return word in re.findall(r"\w+", text.lower())


# graph keyword -> the structured content of its calls, from tests/fixtures/kg/ (made by
# tests/make_kg_fixtures.py: what the Dug interceptor attaches to real Dug results). One kg per
# call; the cited studies ride on the first, the server merges them anyway.
GRAPHS = {}
for _keyword in KEYWORDS:
    _saved = json.loads((OUT / f"{_keyword}.json").read_text(encoding="utf-8"))
    GRAPHS[_keyword] = [{"kg": kg, **({"sources": _saved["sources"]} if i == 0 and _saved["sources"] else {})}
                          for i, kg in enumerate(_saved["kg"])]


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
        kg = {"tool": "get_concept_graph", "args": {"concept_id": "MONDO:0005068"}, "label": "myocardial infarction concept graph",
              "seeds": ["MONDO:0005068"],
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
        # structured content per graph call, as the interceptor attaches it
        attached = [a for word, calls in GRAPHS.items() if _has(question, word) for a in calls] or [{"kg": kg}]
        yield "values", {"messages": [
            AIMessage(content="", tool_calls=[{"name": "search_docs", "args": {"query": question}, "id": "t1"}]
                      + [{"name": a["kg"]["tool"], "args": a["kg"]["args"], "id": f"t{i + 2}"} for i, a in enumerate(attached)]),
            ToolMessage(content=[{"type": "text", "text": json.dumps(c)} for c in chunks], tool_call_id="t1"),
            *[ToolMessage(content="{}", artifact={"structured_content": a}, tool_call_id=f"t{i + 2}")
              for i, a in enumerate(attached)],
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
    import logging
    from contextlib import asynccontextmanager

    import uvicorn

    @asynccontextmanager
    async def noop_lifespan(app):  # skip the real lifespan (needs MCP); graph already set
        # the keywords, as the docstring lists them (an indented word, then a description)
        logging.getLogger("uvicorn.error").info(
            "Stub keywords: %s", " ".join(re.findall(r"^  (\w+) {2,}", __doc__, re.M)))
        yield
    api.app.router.lifespan_context = noop_lifespan
    uvicorn.run(api.app, port=8011)
