import asyncio

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage

from bdc_assist.graph import REJECT, REFUSAL, build_graph
from bdc_assist.prompts import normalize_bdc_names

PREDEFINED = {
    "fisma": {"response": "FISMA canned answer.", "flag": "r"},
    "covid": {"response": "Covid disclaimer.", "flag": "a"},
}


class FakeAgent:
    def __init__(self, reply="Agent answer about BDC."):
        self.reply = reply
        self.called = False

    async def ainvoke(self, payload):
        self.called = True
        return {"messages": [AIMessage(content=self.reply)]}


def run(llm_responses, **state):
    llm = FakeListChatModel(responses=llm_responses)
    agent = FakeAgent()
    graph = build_graph(llm, agent, PREDEFINED)
    result = asyncio.run(graph.ainvoke({"input": "q", "chat_history": [], **state}))
    return result, agent


def test_mcp_tool_retry():
    from langchain_core.tools import StructuredTool

    from bdc_assist.agent import _with_retry

    calls = {"n": 0}

    async def flaky(query: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("boom")
        return "ok"

    tool = StructuredTool.from_function(coroutine=flaky, name="search_docs", description="d")
    _with_retry(tool)
    assert asyncio.run(tool.coroutine(query="x")) == "ok"
    assert calls["n"] == 2


def test_normalize_bdc_names():
    assert normalize_bdc_names("NHLBI BioData Catalyst (BDC) is great") == "BDC is great"
    assert normalize_bdc_names("Use BioData Catalyst today") == "Use BDC today"


def test_blocked_input_skips_everything():
    # llm calls: input guardrail ("Yes" = block)
    state, agent = run(["Yes"])
    assert state["blocked"] is True
    assert state["answer"] == REFUSAL
    assert not agent.called


def test_provider_content_filter_counts_as_block():
    # Azure's jailbreak filter 400s the guardrail call itself — that verdict is a block
    class FilteredLLM:
        async def ainvoke(self, messages):
            raise ValueError("Error code: 400 - {'code': 'content_filter', ...}")

    agent = FakeAgent()
    graph = build_graph(FilteredLLM(), agent, PREDEFINED)
    state = asyncio.run(graph.ainvoke({"input": "jailbreak attempt", "chat_history": []}))
    assert state["blocked"] is True
    assert state["answer"] == REFUSAL
    assert not agent.called


def test_predefined_topic_replaces_answer():
    # llm calls: guardrail "No", classifier "- fisma" (no contextualize call: empty history)
    state, agent = run(["No", "- fisma"])
    assert state["answer"] == "FISMA canned answer."
    assert not agent.called


def test_regular_question_appends_disclaimer():
    # llm calls: guardrail "No", classifier "- covid", output check "Yes"
    state, agent = run(["No", "- covid", "Yes"])
    assert agent.called
    assert state["answer"] == "Agent answer about BDC.\n\nCovid disclaimer."


def test_rejected_answer_gets_reject_reply_without_disclaimer():
    # llm calls: guardrail "No", classifier "- covid", output check "No" → REJECT, no append
    state, agent = run(["No", "- covid", "No"])
    assert agent.called
    assert state["rejected"] is True
    assert state["answer"] == REJECT
