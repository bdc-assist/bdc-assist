import asyncio
import json

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from r_assist import prompts
from r_assist.graph import REJECT, REFUSAL, build_graph

PREDEFINED = {
    "fisma": {"response": "FISMA canned answer.", "flag": "r"},
    "covid": {"response": "Covid disclaimer.", "flag": "a"},
}
# what the fake agent's one search_docs call yields, decoded — two chunks of the same faq
# article (dedupe), one markdown doc without a title (top-heading fallback), and an
# ad-hoc push without a page_url (skipped)
CHUNKS = [
    {"content": "chunk 1", "metadata": {"page_url": "https://x/faq", "title": "FAQ title", "doc_type": "faq"}},
    {"content": "chunk 2", "metadata": {"page_url": "https://x/faq", "title": "FAQ title", "doc_type": "faq"}},
    {"content": "chunk 3", "metadata": {"page_url": "https://x/doc", "hierarchy": "Data Access, Check access",
                                        "doc_type": "docs"}},
    {"content": "chunk 4", "metadata": {"source": "notes.md"}},
]
SOURCES = {prompts.SOURCES_KEY: [{"title": "FAQ title", "link": "https://x/faq", "type": "faq"},
                                 {"title": "Data Access", "link": "https://x/doc", "type": "docs"}]}
SOURCES_MD = "**Sources**\n- [FAQ title](https://x/faq) (faq)\n- [Data Access](https://x/doc) (docs)"
# what an interceptor attached to the fake agent's second tool call (structured content "kg")
KG = {"tool": "get_concept_graph", "args": {"concept_id": "MONDO:1"},
      "nodes": [{"id": "MONDO:1", "name": "mi"}, {"id": "phv1", "name": "MI_EVER"}],
      "edges": [{"subject": "phv1", "object": "MONDO:1"}]}


class FakeAgent:
    """Mirrors the deep agent's astream contract: a tool-calling turn (with
    preamble text), then token chunks of the final answer, then final values."""

    def __init__(self, reply="Agent answer about BDC."):
        self.reply = reply
        self.called = False

    async def astream(self, payload, stream_mode=None):
        self.called = True
        yield "messages", (AIMessageChunk(
            content="Let me look that up.", id="m1",
            tool_call_chunks=[{"name": "search_docs", "args": "", "id": "t1", "index": 0}]), {})
        half = len(self.reply) // 2
        for part in (self.reply[:half], self.reply[half:]):
            yield "messages", (AIMessageChunk(content=part, id="m2"), {})
        yield "values", {"messages": [
            AIMessage(content="", tool_calls=[{"name": "search_docs", "args": {"query": "q"}, "id": "t1"},
                                              {"name": "get_concept_graph", "args": {"concept_id": "MONDO:1"}, "id": "t2"}]),
            # like langchain-mcp-adapters: a list of text blocks, one per chunk, each holding JSON
            ToolMessage(content=[{"type": "text", "text": json.dumps(c)} for c in CHUNKS], tool_call_id="t1"),
            # and structuredContent arrives as the artifact, which the LLM never reads
            ToolMessage(content="{}", artifact={"structured_content": {"kg": KG}}, tool_call_id="t2"),
            AIMessage(content=self.reply)]}


def run(llm_responses, **state):
    llm = FakeListChatModel(responses=llm_responses)
    agent = FakeAgent()
    graph = build_graph(llm, agent, PREDEFINED)
    result = asyncio.run(graph.ainvoke({"input": "q", "chat_history": [], **state}))
    return result, agent


def test_mcp_tool_retry():
    from langchain_core.tools import StructuredTool

    from r_assist.agent import _with_retry

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
    # llm calls: guardrail "No", classifier "- covid", output check "Yes", followups
    state, agent = run(["No", "- covid", "Yes", "- What is BDC?\n- How do I get access?\n- Where are the docs?"])
    assert agent.called
    assert state["answer"] == "Agent answer about BDC.\n\nCovid disclaimer."
    assert state["followups"] == ["What is BDC?", "How do I get access?", "Where are the docs?"]
    assert state["sources"] == SOURCES
    assert state["sources_md"] == SOURCES_MD
    assert state["kg"] == [KG]


def test_sources_follow_project_and_prompts_yaml(monkeypatch):
    # llm calls: guardrail "No", classifier "- other", output check "Yes", followups "- none"
    monkeypatch.setattr(prompts, "SOURCES", "Refs:\n{items}")
    monkeypatch.setattr(prompts, "SOURCES_ITEM", "* {title} <{link}>")
    state, _ = run(["No", "- other", "Yes", "- none"])
    assert state["sources_md"] == "Refs:\n* FAQ title <https://x/faq>\n* Data Access <https://x/doc>"
    monkeypatch.setattr(prompts, "DOC_SEARCH_TOOL", "some_other_tool")
    state, _ = run(["No", "- other", "Yes", "- none"])
    assert state["sources"] == {} and state["sources_md"] == "", "only the configured tool's results are sources"


def test_followups_none_means_empty():
    # llm calls: guardrail "No", classifier "- other", output check "Yes", followups "- none"
    state, agent = run(["No", "- other", "Yes", "- none"])
    assert agent.called
    assert state["followups"] == []


def test_stream_chat_emits_progress_tokens_and_done():
    import json

    from r_assist.api import stream_chat

    # llm calls: guardrail "No", classifier "- covid", output check "Yes", followups "- none"
    llm = FakeListChatModel(responses=["No", "- covid", "Yes", "- none"])
    graph = build_graph(llm, FakeAgent(), PREDEFINED)

    async def collect():
        return [e async for e in stream_chat(graph, "q", [])]

    events = [json.loads(line.removeprefix("data: ")) for line in asyncio.run(collect())]
    # every node start is announced, in workflow order
    assert [e["node"] for e in events if e["type"] == "node"] == [
        "input_guardrail", "contextualize", "classify", "agent",
        "output_guardrail", "append_disclaimer", "suggest_followups"]
    # the agent's tool call surfaces as a status event
    assert {"type": "status", "text": "calling search_docs"} in events
    # sources go out as soon as the agent node ends, before output_guardrail starts
    i_sources = events.index({"type": "sources", "sources": SOURCES, "sources_md": SOURCES_MD, "kg": [KG]})
    i_guard = events.index({"type": "node", "node": "output_guardrail"})
    assert i_sources < i_guard
    # tokens after the last reset are exactly the agent's final response —
    # earlier turns (tool-call preamble) get discarded by the reset
    last_reset = max(i for i, e in enumerate(events) if e["type"] == "reset")
    tokens = [e["text"] for e in events[last_reset:] if e["type"] == "token"]
    assert "".join(tokens) == "Agent answer about BDC."  # no guardrail/classifier chatter mixed in
    assert events[-1] == {"type": "done", "answer": "Agent answer about BDC.\n\nCovid disclaimer.",
                          "blocked": False, "topics": ["covid"], "followups": [],
                          "sources": SOURCES, "sources_md": SOURCES_MD, "kg": [KG]}


def test_rejected_answer_gets_reject_reply_without_disclaimer():
    # llm calls: guardrail "No", classifier "- covid", output check "No" → REJECT, no append
    state, agent = run(["No", "- covid", "No"])
    assert agent.called
    assert state["rejected"] is True
    assert state["answer"] == REJECT
    assert state["sources"] == {} and state["sources_md"] == ""  # no sources under "I couldn't answer"
    assert state["kg"] == []


def test_mcp_servers_yaml():
    from pathlib import Path

    from r_assist.agent import load_mcp_servers

    servers = load_mcp_servers()  # the template config/
    assert servers["r_doc_mcp"]["url"].startswith("http")
    assert all("transport" in v for v in servers.values())

    servers = load_mcp_servers(Path("examples/bdc"))
    assert {"r_doc_mcp", "dug_mcp"} <= set(servers)
    assert all("transport" in v for v in servers.values())
    assert "interceptors" not in servers["dug_mcp"], "r-assist's key, never handed to the transport"


def test_mcp_tool_errors_are_logged(caplog):
    from langchain_core.tools import StructuredTool

    from r_assist.agent import _with_retry

    async def raises(query: str) -> str:
        raise ConnectionError("boom")

    async def error_payload(query: str) -> tuple:  # failure reported as a normal result, in the adapter's (content, artifact) shape
        return [{"type": "text", "text": '{\n  "error": "Error executing tool: NoneType has no query"\n}'}], None

    async def warnings_payload(query: str) -> tuple:  # empty data plus warnings, the other soft-failure shape
        return [{"type": "text", "text": '{"total": 0, "items": [], "warnings": ["upstream returned HTTP 404 for \'x1\'"]}'}], None

    with caplog.at_level("WARNING", logger="r_assist.agent"):
        tool = _with_retry(StructuredTool.from_function(coroutine=error_payload, name="search_a", description="d"))
        asyncio.run(tool.coroutine(query="heart attack"))
        tool = _with_retry(StructuredTool.from_function(coroutine=warnings_payload, name="search_b", description="d"))
        asyncio.run(tool.coroutine(query="x"))
        tool = _with_retry(StructuredTool.from_function(coroutine=raises, name="search_c", description="d"))
        try:
            asyncio.run(tool.coroutine(query="x"))
        except ConnectionError:
            pass
    msgs = [r.getMessage() for r in caplog.records]
    assert "search_a{'query': 'heart attack'} returned an error: Error executing tool: NoneType has no query" in msgs
    assert "search_b{'query': 'x'} returned an error: upstream returned HTTP 404 for 'x1'" in msgs
    assert sum("search_c{'query': 'x'} raised ConnectionError: boom" in m for m in msgs) == 2
