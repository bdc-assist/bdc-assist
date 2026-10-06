import asyncio
import json

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from bdc_assist.graph import REJECT, REFUSAL, _dug_sources, _knowledge_graph, _sources_md, build_graph
from bdc_assist.prompts import normalize_bdc_names

PREDEFINED = {
    "fisma": {"response": "FISMA canned answer.", "flag": "r"},
    "covid": {"response": "Covid disclaimer.", "flag": "a"},
}
# what the fake agent's one search_docs call yields, decoded — two chunks of the same faq
# article (dedupe) and one gitbook doc without a title (breadcrumb fallback)
CHUNKS = [
    {"content": "chunk 1", "metadata": {"page_url": "https://x/faq", "title": "FAQ title", "doc_type": "faq"}},
    {"content": "chunk 2", "metadata": {"page_url": "https://x/faq", "title": "FAQ title", "doc_type": "faq"}},
    {"content": "chunk 3", "metadata": {"page_url": "https://x/doc", "hierarchy": ["Data Access", "Check access"],
                                        "doc_type": "docs"}},
]
TOOL_RESULT = {"tool": "search_docs", "args": {"query": "q"}, "result": CHUNKS}
SOURCES = {"bdc-doc": [{"title": "FAQ title", "link": "https://x/faq", "type": "faq"},
                       {"title": "Check access", "link": "https://x/doc", "type": "docs"}]}
SOURCES_MD = "**Sources**\n- [FAQ title](https://x/faq) (faq)\n- [Check access](https://x/doc) (docs)"


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
            AIMessage(content="", tool_calls=[{"name": "search_docs", "args": {"query": "q"}, "id": "t1"}]),
            # like langchain-mcp-adapters: a list of text blocks, one per chunk, each holding JSON
            ToolMessage(content=[{"type": "text", "text": json.dumps(c)} for c in CHUNKS], tool_call_id="t1"),
            AIMessage(content=self.reply)]}


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


def test_mcp_tool_errors_are_logged(caplog):
    from langchain_core.tools import StructuredTool

    from bdc_assist.agent import _with_retry

    async def raises(query: str) -> str:
        raise ConnectionError("boom")

    async def error_payload(query: str) -> tuple:  # dug-mcp style failure, in the adapter's (content, artifact) shape
        return [{"type": "text", "text": '{\n  "error": "Error executing tool: NoneType has no query"\n}'}], None

    async def warnings_payload(query: str) -> tuple:  # empty data + warnings, dug-mcp's other failure shape
        return [{"type": "text", "text": '{"total_variables_found": 0, "variables": [], "warnings": ["PIC-SURE returned HTTP 404 for \'phv1\'"]}'}], None

    with caplog.at_level("WARNING", logger="bdc_assist.agent"):
        tool = _with_retry(StructuredTool.from_function(coroutine=error_payload, name="search_concepts", description="d"))
        asyncio.run(tool.coroutine(query="heart attack"))
        tool = _with_retry(StructuredTool.from_function(coroutine=warnings_payload, name="find_cohort_variables", description="d"))
        asyncio.run(tool.coroutine(query="x"))
        tool = _with_retry(StructuredTool.from_function(coroutine=raises, name="picsure_search", description="d"))
        try:
            asyncio.run(tool.coroutine(query="x"))
        except ConnectionError:
            pass
    msgs = [r.getMessage() for r in caplog.records]
    assert any("search_concepts{'query': 'heart attack'} returned an error: Error executing tool: NoneType has no query" == m for m in msgs)
    assert any("find_cohort_variables{'query': 'x'} returned an error: PIC-SURE returned HTTP 404 for 'phv1'" == m for m in msgs)
    assert sum("picsure_search{'query': 'x'} raised ConnectionError: boom" in m for m in msgs) == 2


def test_mcp_servers_yaml():
    from bdc_assist.agent import load_mcp_servers

    servers = load_mcp_servers()
    assert servers["bdc_doc_mcp"]["url"].startswith("http")
    assert all("transport" in v for v in servers.values())


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
    # llm calls: guardrail "No", classifier "- covid", output check "Yes", followups
    state, agent = run(["No", "- covid", "Yes", "- What is BDC?\n- How do I get access?\n- Where are the docs?"])
    assert agent.called
    assert state["answer"] == "Agent answer about BDC.\n\nCovid disclaimer."
    assert state["followups"] == ["What is BDC?", "How do I get access?", "Where are the docs?"]
    assert state["tool_results"] == [TOOL_RESULT]
    assert state["sources"] == SOURCES
    assert state["sources_md"] == SOURCES_MD


def test_followups_none_means_empty():
    # llm calls: guardrail "No", classifier "- other", output check "Yes", followups "- none"
    state, agent = run(["No", "- other", "Yes", "- none"])
    assert agent.called
    assert state["followups"] == []


def test_stream_chat_emits_progress_tokens_and_done():
    import json

    from bdc_assist.api import stream_chat

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
    i_sources = events.index({"type": "sources", "sources": SOURCES, "sources_md": SOURCES_MD})
    i_guard = events.index({"type": "node", "node": "output_guardrail"})
    assert i_sources < i_guard
    # tokens after the last reset are exactly the agent's final response —
    # earlier turns (tool-call preamble) get discarded by the reset
    last_reset = max(i for i, e in enumerate(events) if e["type"] == "reset")
    tokens = [e["text"] for e in events[last_reset:] if e["type"] == "token"]
    assert "".join(tokens) == "Agent answer about BDC."  # no guardrail/classifier chatter mixed in
    assert events[-1] == {"type": "done", "answer": "Agent answer about BDC.\n\nCovid disclaimer.",
                          "blocked": False, "topics": ["covid"], "followups": [],
                          "tool_results": [TOOL_RESULT], "sources": SOURCES, "sources_md": SOURCES_MD,
                          "graph": {}}
    # no tool returned a graph, so no graph event
    assert not any(e["type"] == "graph" for e in events)


def test_rejected_answer_gets_reject_reply_without_disclaimer():
    # llm calls: guardrail "No", classifier "- covid", output check "No" → REJECT, no append
    state, agent = run(["No", "- covid", "No"])
    assert agent.called
    assert state["rejected"] is True
    assert state["answer"] == REJECT
    # the rejected answer's sources must not decorate the canned reply
    assert state["sources"] == {}
    assert state["sources_md"] == ""
    assert state["graph"] == {}


def test_knowledge_graph_from_dug_concept_graph():
    def row(vid, sid):
        return {"concept": "chd", "concept_id": "C", "concept_type": "biolink:NamedThing",
                "variable_name": f"name {vid}", "variable_id": vid,
                "study_name": f"study {sid}", "study_id": sid, "related_concepts_count": 3}

    call = {"tool": "get_concept_graph", "args": {"concept_id": "C"},
            "result": {"concept_id": "C", "graph": [row("v1", "s1"), row("v2", "s1")]}}
    again = {**call, "result": {"graph": [row("v2", "s1")]}}  # same nodes and edges twice
    other = {"tool": "search_concepts", "args": {}, "result": {"graph": [row("v9", "s9")]}}  # wrong tool
    graph = _knowledge_graph([TOOL_RESULT, call, again, other])
    assert graph["nodes"] == [
        {"id": "C", "label": "chd", "type": "concept", "concept_type": "biolink:NamedThing"},  # verbatim
        {"id": "v1", "label": "name v1", "type": "variable", "related_concepts_count": 3},
        {"id": "s1", "label": "study s1", "type": "study"},
        {"id": "v2", "label": "name v2", "type": "variable", "related_concepts_count": 3}]
    assert graph["edges"] == [{"source": "v1", "target": "C"}, {"source": "v1", "target": "s1"},
                              {"source": "v2", "target": "C"}, {"source": "v2", "target": "s1"}]
    assert _knowledge_graph([TOOL_RESULT, other]) == {}


def test_dug_sources_merge_and_dedupe():
    fhs = {"title": "Framingham Cohort", "link": "https://x/study?phs000007", "type": "dbgap-study"}
    aric = {"title": "ARIC", "link": "https://x/study?phs000280", "type": "dbgap-study"}
    results = [
        TOOL_RESULT,  # doc chunks: no _sources
        {"tool": "get_concept_graph", "args": {}, "result": {"graph": [], "_sources": [fhs]}},
        {"tool": "get_concept_graph", "args": {}, "result": {"graph": [], "_sources": [aric, fhs]}},
    ]
    assert _dug_sources(results) == [fhs, aric]  # Framingham once, first-seen order
    assert _dug_sources([TOOL_RESULT]) == []
    # in the markdown list after the docs, like any other source
    md = _sources_md(SOURCES["bdc-doc"] + [fhs])
    assert md.endswith("\n- [Framingham Cohort](https://x/study?phs000007) (dbgap-study)")
