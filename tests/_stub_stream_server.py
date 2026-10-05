"""Throwaway stub for eyeballing tests/ui/demo.html and tests/ui/kg_demo.html without real services:
serves the real api app on :8011 with a fake slow agent. Run: uv run python tests/_stub_stream_server.py
then open tests/ui/demo.html?api=http://127.0.0.1:8011 (or kg_demo.html?api=...)"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # runnable from anywhere

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

import r_assist.api as api
from r_assist import prompts
from r_assist.graph import build_graph


class SlowAgent:
    async def astream(self, payload, stream_mode=None):
        yield "messages", (AIMessageChunk(
            content="Let me look that up. ", id="m1",
            tool_call_chunks=[{"name": "search_docs", "args": "", "id": "t1", "index": 0}]), {})
        await asyncio.sleep(1.5)
        words = ("This project's **documentation chatbot** answers questions about the configured docs. "
                 "See [getting started](https://example.org/docs/start).").split()
        for w in words:
            await asyncio.sleep(0.12)
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
              "nodes": [{"id": "MONDO:0005068", "name": "myocardial infarction", "category": "Disease"},
                        {"id": "phv1", "name": "MI_EVER", "category": "StudyVariable",
                         "description": "Ever told by a doctor you had a heart attack?"},
                        {"id": "phv2", "name": "MI_AGE", "category": "StudyVariable"},
                        {"id": "phv3", "name": "ECG_MI", "category": "StudyVariable"},
                        {"id": "phs000007", "name": "Framingham Cohort", "category": "Study"},
                        {"id": "phs000280", "name": "Atherosclerosis Risk in Communities (ARIC) Cohort", "category": "Study"}],
              "edges": [{"subject": v, "object": "MONDO:0005068", "predicate": "related_to"} for v in ("phv1", "phv2", "phv3")]
                       + [{"subject": "phv1", "object": "phs000007"}, {"subject": "phv2", "object": "phs000007"},
                          {"subject": "phv3", "object": "phs000280"}]}
        yield "values", {"messages": [
            AIMessage(content="", tool_calls=[{"name": "search_docs", "args": {"query": payload["messages"][0]["content"]}, "id": "t1"},
                                              {"name": "get_concept_graph", "args": kg["args"], "id": "t2"}]),
            ToolMessage(content=[{"type": "text", "text": json.dumps(c)} for c in chunks], tool_call_id="t1"),
            ToolMessage(content="{}", artifact={"structured_content": {"kg": kg}}, tool_call_id="t2"),
            AIMessage(content=" ".join(words)),
        ]}


class ScriptedLLM:
    """Answers by which prompt it receives, not call order. (A response-list fake
    goes out of phase on turn 2: contextualize starts consuming responses once
    chat history exists, so the output guardrail reads the followups list as its
    yes/no verdict and rejects every follow-up.)"""

    _CLASSIFIER = prompts.topic_classifier_system(["covid"])

    async def ainvoke(self, messages):
        role, text = messages[0]
        if text == prompts.INPUT_GUARDRAIL_SYSTEM:
            reply = "No"
        elif text == prompts.CONTEXTUALIZE_SYSTEM:
            reply = messages[-1][1]  # echo the question unchanged
        elif text == self._CLASSIFIER:
            reply = "- covid"
        elif text.startswith(prompts.OUTPUT_GUARDRAIL_HUMAN.split("{")[0]):
            await asyncio.sleep(2)  # slow on purpose: shows the answer + sources rendered before "done"
            reply = "Yes"
        elif text.startswith(prompts.SUGGEST_FOLLOWUPS_HUMAN.split("{")[0]):
            await asyncio.sleep(2)
            reply = "- What is dbGaP?\n- How do I get access?"
        else:
            raise AssertionError(f"unexpected prompt: {text[:80]}")
        return AIMessage(content=reply)


api.graph = build_graph(ScriptedLLM(), SlowAgent(), {"covid": {"response": "Covid disclaimer.", "flag": "a"}})

if __name__ == "__main__":
    from contextlib import asynccontextmanager

    import uvicorn

    @asynccontextmanager
    async def noop_lifespan(app):  # skip the real lifespan (needs MCP); graph already set
        yield
    api.app.router.lifespan_context = noop_lifespan
    uvicorn.run(api.app, port=8011)
