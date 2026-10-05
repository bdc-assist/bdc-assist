"""Throwaway stub for eyeballing the web UI (web/) or tests/streaming_demo.html
without real services: serves the real api app on :8011 with a fake slow agent
and scripted LLM. Run: uv run python tests/_stub_stream_server.py, then
VITE_API_URL=http://127.0.0.1:8011 npm --prefix web run dev
(or open streaming_demo.html?api=http://127.0.0.1:8011).

A keyword anywhere in the question picks the path (otherwise a normal answer):
  block      input guardrail refuses: canned REFUSAL, blocked
  reject     output guardrail rejects the streamed draft: canned REJECT, no sources
  canned     an "r" topic matches: its canned reply, agent skipped
  covid      an "a" topic matches: disclaimer appended to the answer
  nosources  the agent answers without searching: no sources
  crash      the agent fails mid-answer: the stream breaks off without "done"
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # runnable from anywhere

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

import bdc_assist.api as api
from bdc_assist import prompts
from bdc_assist.graph import build_graph


def _has(text: str, word: str) -> bool:
    return word in text.lower()


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
        words = ("**BDC** (BioData Catalyst) is NHLBI's cloud platform for heart, lung, blood, and sleep "
                 "research data. See the [overview](https://biodatacatalyst.nhlbi.nih.gov/about/overview).").split()
        for i, w in enumerate(words):
            await asyncio.sleep(0.12)
            if i == 8 and _has(question, "crash"):
                raise RuntimeError("stub: agent crashed mid-answer")
            yield "messages", (AIMessageChunk(content=w + " ", id="m2"), {})
        # final state like the real agent: the tool call, its doc chunks, the answer
        chunks = [
            {"content": "BDC is ...", "score": 0.7, "metadata": {"page_url": "https://biodatacatalyst.nhlbi.nih.gov/about/overview",
                                                                  "doc_type": "page", "headings": "Overview, Mission"}},
            {"content": "BDC offers ...", "score": 0.8, "metadata": {"page_url": "https://bdcatalyst.freshdesk.com/support/solutions/articles/60000541522",
                                                                     "doc_type": "faq", "title": "What can BDC offer me?"}},
            {"content": "BDC is ... (again)", "score": 0.9, "metadata": {"page_url": "https://biodatacatalyst.nhlbi.nih.gov/about/overview",
                                                                          "doc_type": "page", "headings": "Overview"}},
        ]
        yield "values", {"messages": [
            AIMessage(content="", tool_calls=[{"name": "search_docs", "args": {"query": payload["messages"][0]["content"]}, "id": "t1"}]),
            ToolMessage(content=[{"type": "text", "text": json.dumps(c)} for c in chunks], tool_call_id="t1"),
            AIMessage(content=" ".join(words)),
        ]}

    async def _answer_without_search(self):
        words = "BDC stands for BioData Catalyst. No documents were needed for that.".split()
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
            # the template itself says "block", so look only at the filled-in input
            before, after = prompts.INPUT_GUARDRAIL_HUMAN.split("{input}")
            user_input = question[len(before):len(question) - len(after)]
            reply = "Yes" if _has(user_input, "block") else "No"
        elif text == prompts.CONTEXTUALIZE_SYSTEM:
            reply = question  # echo the question unchanged
        elif text == self._CLASSIFIER:
            reply = "\n".join(f"- {t}" for t in TOPICS if _has(question, t)) or "- none"
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
