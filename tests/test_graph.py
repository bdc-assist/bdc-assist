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


def _blocks(text):
    """Message content on the Responses API (gpt-6-luna reasoning): a list of blocks, not a string."""
    return [{"type": "reasoning", "summary": []}, {"type": "text", "text": text}]


class FakeAgent:
    """Mirrors the deep agent's astream contract: a tool-calling turn (with
    preamble text), then token chunks of the final answer, then final values.
    blocks=True: every AI message's content comes as Responses API blocks."""

    def __init__(self, reply="Agent answer about BDC.", blocks=False):
        self.reply = reply
        self.called = False
        self.wrap = _blocks if blocks else (lambda text: text)

    async def astream(self, payload, stream_mode=None):
        self.called = True
        self.payload = payload
        yield "messages", (AIMessageChunk(
            content=self.wrap("Let me look that up."), id="m1",
            tool_call_chunks=[{"name": "search_docs", "args": "", "id": "t1", "index": 0}]), {})
        half = len(self.reply) // 2
        for part in (self.reply[:half], self.reply[half:]):
            yield "messages", (AIMessageChunk(content=self.wrap(part), id="m2"), {})
        yield "values", {"messages": [
            AIMessage(content="", tool_calls=[{"name": "search_docs", "args": {"query": "q"}, "id": "t1"},
                                              {"name": "get_concept_graph", "args": {"concept_id": "MONDO:1"}, "id": "t2"}]),
            # like langchain-mcp-adapters: a list of text blocks, one per chunk, each holding JSON
            ToolMessage(content=[{"type": "text", "text": json.dumps(c)} for c in CHUNKS], tool_call_id="t1"),
            # and structuredContent arrives as the artifact, which the LLM never reads
            ToolMessage(content="{}", artifact={"structured_content": {"kg": KG}}, tool_call_id="t2"),
            AIMessage(content=self.wrap(self.reply))]}


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
                          "sources": SOURCES, "sources_md": SOURCES_MD, "kg": [KG], "mcp_errors": []}


def test_blocked_answer_gets_reject_reply_without_disclaimer():
    # llm calls: guardrail "No", classifier "- covid", output check "No" → REJECT, no append
    state, agent = run(["No", "- covid", "No"])
    assert agent.called
    assert state["blocked"] is True
    assert state["answer"] == REJECT
    assert state["sources"] == {} and state["sources_md"] == ""  # no sources under "I couldn't answer"
    assert state["kg"] == []


def test_output_content_filter_counts_as_block():
    # the provider's filter 400s the output check (i.e. the answer) — a block, not a crash
    class FilteredOutputLLM:
        replies = iter(["No", "- other"])  # input guardrail, classifier

        async def ainvoke(self, messages):
            reply = next(self.replies, None)
            if reply is None:
                raise ValueError("Error code: 400 - {'code': 'content_filter', ...}")
            return AIMessage(content=reply)

    graph = build_graph(FilteredOutputLLM(), FakeAgent(), PREDEFINED)
    state = asyncio.run(graph.ainvoke({"input": "q", "chat_history": []}))
    assert state["blocked"] is True
    assert state["answer"] == REJECT


def test_stream_blocked_answer_replaced_in_done():
    from r_assist.api import stream_chat

    # llm calls: guardrail "No", classifier "- covid", output check "No"
    graph = build_graph(FakeListChatModel(responses=["No", "- covid", "No"]), FakeAgent(), PREDEFINED)

    async def collect():
        return [json.loads(e.removeprefix("data: ")) async for e in stream_chat(graph, "q", [])]

    events = asyncio.run(collect())
    assert any(e["type"] == "token" for e in events)  # the provisional answer did stream
    assert events[-2] == {"type": "node", "node": "output_guardrail"}  # nothing runs after the block
    assert events[-1] == {"type": "done", "answer": REJECT, "blocked": True, "topics": ["covid"],
                          "followups": [], "sources": {}, "sources_md": "", "kg": [],
                          "mcp_errors": []}


def test_stream_reports_a_failed_run(caplog):
    """The agent failing mid-answer (LLM error, recursion limit) ends the stream with an error
    event and a logged traceback; the exception used to cut the HTTP stream, which the browser
    reported as a network error ("is r-assist running?")."""
    from r_assist.api import stream_chat

    class FailingAgent(FakeAgent):
        async def astream(self, payload, stream_mode=None):
            yield "messages", (AIMessageChunk(content="Half an ans", id="m1"), {})
            raise RuntimeError("model gateway down")

    # llm calls: input guardrail, classifier (no history: contextualize makes none)
    graph = build_graph(FakeListChatModel(responses=["No", "- none"]), FailingAgent(), PREDEFINED)

    async def collect():
        return [json.loads(e.removeprefix("data: ")) async for e in stream_chat(graph, "q", [])]

    events = asyncio.run(collect())
    assert events[-1] == {"type": "error"} and not any(e["type"] == "done" for e in events), events
    assert "model gateway down" in caplog.text


def test_chat_endpoint_with_history(monkeypatch):
    """POST /chat through FastAPI: the client's history reaches contextualize, the agent gets its
    rewrite, and the response carries the answer, sources, kg and mcp_errors."""
    from fastapi.testclient import TestClient

    from r_assist import api

    agent = FakeAgent()
    # llm calls: input guardrail, contextualize (history present), classifier, output check, follow-ups
    llm = FakeListChatModel(responses=["No", "How do I get access to PIC-SURE?", "- other", "Yes", "- none"])
    monkeypatch.setattr(api, "graph", build_graph(llm, agent, PREDEFINED))
    monkeypatch.setattr(api, "mcp_errors", ["dug_mcp: ConnectError: x"])
    history = [{"role": "user", "content": "What is PIC-SURE?"}, {"role": "assistant", "content": "A BDC tool."}]

    res = TestClient(api.app).post("/chat", json={"input": "How do I get access to it?", "chat_history": history})
    assert res.status_code == 200, res.text
    assert agent.payload["messages"][0]["content"] == "How do I get access to PIC-SURE?"
    assert res.json() == {"answer": "Agent answer about BDC.", "blocked": False, "topics": [], "followups": [],
                          "sources": SOURCES, "sources_md": SOURCES_MD, "kg": [KG],
                          "mcp_errors": ["dug_mcp: ConnectError: x"]}


def test_responses_api_content_blocks():
    """COMPLETION_REASONING_EFFORT above none puts the model on the Responses API, where message
    content is a list of blocks (reasoning, text, function_call), not a string. Guardrail verdicts,
    topics, the answer and the streamed tokens all come from the text blocks."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    from r_assist.api import stream_chat

    replies = ["No", "- covid", "Yes", "- none"]  # input guardrail, classifier, output check, follow-ups
    llm = GenericFakeChatModel(messages=iter([AIMessage(content=_blocks(r)) for r in replies]))
    graph = build_graph(llm, FakeAgent(blocks=True), PREDEFINED)

    async def collect():
        return [json.loads(e.removeprefix("data: ")) async for e in stream_chat(graph, "q", [])]

    events = asyncio.run(collect())
    last_reset = max(i for i, e in enumerate(events) if e["type"] == "reset")
    assert "".join(e["text"] for e in events[last_reset:] if e["type"] == "token") == "Agent answer about BDC."
    done = events[-1]
    assert done["answer"] == "Agent answer about BDC.\n\nCovid disclaimer."
    assert (done["blocked"], done["topics"], done["followups"]) == (False, ["covid"], [])


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
    assert "stand_ins" not in servers["dug_mcp"], "r-assist's key, never handed to the transport"


def test_unreachable_mcp_server_is_skipped(monkeypatch, caplog):
    from langchain_core.tools import StructuredTool
    from langchain_mcp_adapters.client import MultiServerMCPClient

    from r_assist import agent

    async def search(query: str) -> str:
        return "ok"

    async def get_tools(self, *, server_name=None):
        if server_name == "dug_mcp":  # what the adapter raises off the VPN
            raise ExceptionGroup("unhandled errors in a TaskGroup",
                                 [ConnectionError("Client error '403 Forbidden'\nFor more information")])
        return [StructuredTool.from_function(coroutine=search, name="search_docs", description="d")]

    monkeypatch.setattr(MultiServerMCPClient, "get_tools", get_tools)
    monkeypatch.setattr(agent, "_servers_yaml", lambda data_dir: {
        "r_doc_mcp": {}, "dug_mcp": {"stand_ins": ["search_concepts", "get_concept_graph"]}})
    monkeypatch.setattr(agent, "create_deep_agent", lambda llm, tools, **kw: tools)

    tools, errors = asyncio.run(agent.build_agent(None))
    assert errors == ["dug_mcp: ConnectionError: Client error '403 Forbidden'"]
    assert "dug_mcp" in caplog.text
    # the prompt names dug's tools: their stand-ins error, so the agent says the service is down
    # instead of passing off doc search results as catalog results
    assert [t.name for t in tools] == ["search_docs", "search_concepts", "get_concept_graph"]
    result = asyncio.run(tools[1].ainvoke({"search_term": "heart attack", "find_variables": True}))
    assert result.startswith("Error: the dug_mcp service is unavailable right now.")


def test_mcp_servers_rechecked(monkeypatch, caplog):
    """A failed tool call (recheck) re-checks the servers without waiting out MCP_RETRY_SECONDS:
    one that went down mid-session drops out, one that was down comes back, and a broken
    reload keeps the loop alive."""
    import pytest

    from r_assist import api

    recheck = asyncio.Event()
    builds = iter([ValueError("bad yaml"),
                   ("agent1", ["r_doc_mcp: ConnectError: x"]),  # went down mid-session
                   ("agent2", [])])                              # back

    async def build_agent(llm):
        result = next(builds, None)
        if result is None:
            raise asyncio.CancelledError  # shutdown
        recheck.set()  # each check sees another failed tool call: none may wait out the hour
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(api, "build_agent", build_agent)
    monkeypatch.setattr(api, "build_graph", lambda llm, agent, predefined: agent)
    monkeypatch.setattr(api, "recheck", recheck)
    monkeypatch.setattr(api, "MCP_RETRY_SECONDS", 3600)
    monkeypatch.setattr(api, "graph", "agent0")
    monkeypatch.setattr(api, "mcp_errors", [])
    recheck.set()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(api.retry_mcp(None, {}))
    assert (api.graph, api.mcp_errors) == ("agent2", [])
    assert "MCP reload failed" in caplog.text
    assert "MCP server back, its tools loaded: r_doc_mcp" in caplog.text
    assert asyncio.run(api.health()) == {"status": "ok", "mcp_errors": []}


def test_mcp_tool_errors_are_logged(caplog):
    import pytest
    from langchain_core.tools import StructuredTool, ToolException

    from r_assist import agent
    from r_assist.agent import _with_retry

    async def raises(query: str) -> str:
        raise ConnectionError("boom")

    async def tool_error(query: str) -> str:  # the server answered isError=True: the adapter's to report
        raise ToolException("Error executing tool search_d: Failed to connect to Ollama")

    async def error_payload(query: str) -> tuple:  # failure reported as a normal result, in the adapter's (content, artifact) shape
        return [{"type": "text", "text": '{\n  "error": "Error executing tool: NoneType has no query"\n}'}], None

    async def warnings_payload(query: str) -> tuple:  # empty data plus warnings, the other soft-failure shape
        return [{"type": "text", "text": '{"total": 0, "items": [], "warnings": ["upstream returned HTTP 404 for \'x1\'"]}'}], None

    with caplog.at_level("WARNING", logger="r_assist.agent"):
        tool = _with_retry(StructuredTool.from_function(coroutine=error_payload, name="search_a", description="d"))
        asyncio.run(tool.coroutine(query="heart attack"))
        tool = _with_retry(StructuredTool.from_function(coroutine=warnings_payload, name="search_b", description="d"))
        asyncio.run(tool.coroutine(query="x"))
        tool = _with_retry(StructuredTool.from_function(coroutine=tool_error, name="search_d", description="d"))
        with pytest.raises(ToolException):
            asyncio.run(tool.coroutine(query="x"))
        assert not agent.recheck.is_set(), "the server answered: nothing to re-check"
        # a server gone mid-session: the agent gets an error result instead of the run crashing
        tool = _with_retry(StructuredTool.from_function(coroutine=raises, name="search_c", description="d",
                                                        response_format="content_and_artifact"))
        content, artifact = asyncio.run(tool.coroutine(query="x"))
    msgs = [r.getMessage() for r in caplog.records]
    assert "search_a{'query': 'heart attack'} returned an error: Error executing tool: NoneType has no query" in msgs
    assert "search_b{'query': 'x'} returned an error: upstream returned HTTP 404 for 'x1'" in msgs
    assert sum("search_c{'query': 'x'} raised ConnectionError: boom" in m for m in msgs) == 2
    # an error, not "answer without it": the prompts' error rule then says unavailable, no guessing
    assert content == "Error: search_c failed (ConnectionError: boom); its server may be down."
    assert artifact is None
    assert agent.recheck.is_set(), "a failed call re-checks the servers"
    agent.recheck.clear()


def test_attached_sources_join_the_doc_sources():
    """An interceptor can attach sources to a tool result (structured content "sources",
    {key: [...]}): they're listed under their key, deduplicated on link across calls, and
    follow the doc sources in sources_md."""
    fhs = {"title": "Framingham Cohort", "link": "https://x/study?phs000007", "type": "dbgap-study"}
    aric = {"title": "ARIC", "link": "https://x/study?phs000280", "type": "dbgap-study"}

    class CitingAgent(FakeAgent):
        async def astream(self, payload, stream_mode=None):
            async for mode, chunk in super().astream(payload, stream_mode):
                if mode == "values":
                    msgs = chunk["messages"]
                    chunk = {"messages": [*msgs[:-1],
                        ToolMessage(content="{}", artifact={"structured_content": {"sources": {"dug": [fhs]}}},
                                    tool_call_id="t3"),
                        ToolMessage(content="{}", artifact={"structured_content": {"sources": {"dug": [aric, fhs]}}},
                                    tool_call_id="t4"),
                        msgs[-1]]}
                yield mode, chunk

    llm = FakeListChatModel(responses=["No", "- none", "Yes", "- none"])
    state = asyncio.run(build_graph(llm, CitingAgent(), PREDEFINED).ainvoke({"input": "q", "chat_history": []}))
    assert state["sources"] == {**SOURCES, "dug": [fhs, aric]}
    assert state["sources_md"] == (SOURCES_MD + "\n- [Framingham Cohort](https://x/study?phs000007) (dbgap-study)"
                                               "\n- [ARIC](https://x/study?phs000280) (dbgap-study)")
