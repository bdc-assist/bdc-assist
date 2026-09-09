"""Throwaway stub for eyeballing tests/streaming_demo.html without real services:
serves the real api app on :8011 with a fake slow agent. Run: uv run python tests/_stub_stream_server.py
then open streaming_demo.html?api=http://127.0.0.1:8011"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # runnable from anywhere

from langchain_core.messages import AIMessage, AIMessageChunk

import bdc_assist.api as api
from bdc_assist import prompts
from bdc_assist.graph import build_graph


class SlowAgent:
    async def astream(self, payload, stream_mode=None):
        yield "messages", (AIMessageChunk(
            content="Let me look that up. ", id="m1",
            tool_call_chunks=[{"name": "search_docs", "args": "", "id": "t1", "index": 0}]), {})
        await asyncio.sleep(1.5)
        words = "BDC (BioData Catalyst) is NHLBI's cloud platform for heart, lung, blood, and sleep research data.".split()
        for w in words:
            await asyncio.sleep(0.12)
            yield "messages", (AIMessageChunk(content=w + " ", id="m2"), {})
        yield "values", {"messages": [AIMessage(content=" ".join(words))]}


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
            reply = "Yes"
        elif text.startswith(prompts.SUGGEST_FOLLOWUPS_HUMAN.split("{")[0]):
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
