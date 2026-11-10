import asyncio
import json
import time
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from r_assist import prompts
from r_assist.graph import REJECT, REFUSAL, build_graph, load_nodes, node_fields

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
# plus the "covid" disclaimer, when classify matches it (PREDEFINED gives it no title or link)
COVID_SOURCES = SOURCES | {"predefined": [{"title": "covid", "link": "", "type": "predefined"}]}
COVID_SOURCES_MD = SOURCES_MD + "\n- covid (predefined)"
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
        # the call's arguments stream on in chunks with no name nor text; tool results stream too
        yield "messages", (AIMessageChunk(content="", id="m1", tool_call_chunks=[
            {"name": None, "args": '{"query": "q"}', "id": None, "index": 0}]), {})
        yield "messages", (ToolMessage(content="[]", tool_call_id="t1"), {})
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


class StepLLM:
    """Fake completion LLM answering by workflow step, not call order: the input guardrail and the
    classifier run side by side, so which calls first varies. replies are in workflow order (input
    guardrail, contextualize when there is history, classifier, output check, follow-ups, related
    terms, related papers' fit); a step past the end gets "". wrap: e.g. _blocks, for Responses API
    content. seen: each step's prompt text, as asked."""
    STEPS = ["guardrail", "contextualize", "classify", "output", "followups", "related", "filter"]

    def __init__(self, replies, wrap=lambda text: text):
        self.replies, self.wrap, self.contextualized, self.seen = list(replies), wrap, False, {}

    def step(self, messages):
        role, text = messages[0]
        if text == prompts.INPUT_GUARDRAIL_SYSTEM:
            return "guardrail"
        if text == prompts.CONTEXTUALIZE_SYSTEM:
            return "contextualize"
        if role == "system":
            return "classify"
        for step, key in (("related", "related_keywords_human"), ("filter", "related_filter_human")):
            if prompts.PROMPTS.get(key) and text.startswith(prompts.PROMPTS[key].split("{")[0]):
                return step
        return "output" if text.startswith(prompts.OUTPUT_GUARDRAIL_HUMAN.split("{")[0]) else "followups"

    async def ainvoke(self, messages):
        step = self.step(messages)
        self.seen[step] = messages[-1][1]
        self.contextualized |= step == "contextualize"  # it runs a step before the others
        order = [s for s in self.STEPS if s != "contextualize" or self.contextualized]
        i = order.index(step)
        return AIMessage(content=self.wrap(self.replies[i] if i < len(self.replies) else ""))


def run(llm_responses, **state):
    llm = StepLLM(llm_responses)
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
    # llm calls: input guardrail ("Yes" = block), and the classifier alongside it (reply discarded)
    state, agent = run(["Yes"])
    assert state["blocked"] == "input"
    assert state["answer"] == REFUSAL
    assert not agent.called


def test_a_blocked_input_wins_over_a_canned_topic():
    # the classifier matches "fisma" (flag r) in the same step the guardrail blocks: the refusal
    # stands, with none of the canned answer's topics or sources
    state, agent = run(["Yes", "- fisma"])
    assert state["blocked"] == "input" and state["answer"] == REFUSAL and not agent.called
    assert state["topics"] == [] and state["sources"] == {} and state["sources_md"] == ""


def test_guardrail_and_classifier_run_side_by_side():
    # each model call takes 0.3s; one after the other the two would take 0.6s before the agent
    class SlowStepLLM(StepLLM):
        async def ainvoke(self, messages):
            await asyncio.sleep(0.3)
            return await super().ainvoke(messages)

    graph = build_graph(SlowStepLLM(["No", "- fisma"]), FakeAgent(), PREDEFINED)  # canned: ends after them
    start = time.monotonic()
    state = asyncio.run(graph.ainvoke({"input": "q", "chat_history": []}))
    assert state["answer"] == "FISMA canned answer."
    assert time.monotonic() - start < 0.5, "the guardrail and the classifier ran one after the other"


def test_provider_content_filter_counts_as_block():
    # Azure's jailbreak filter 400s the guardrail call itself — that verdict is a block. The
    # classifier (and contextualize, with history) sends the same text and is filtered too: they
    # run before or beside the guardrail now, so that must not crash the run
    class FilteredLLM:
        async def ainvoke(self, messages):
            raise ValueError("Error code: 400 - {'code': 'content_filter', ...}")

    agent = FakeAgent()
    graph = build_graph(FilteredLLM(), agent, PREDEFINED)
    for history in ([], [("user", "hi"), ("assistant", "hello")]):
        state = asyncio.run(graph.ainvoke({"input": "jailbreak attempt", "chat_history": history}))
        assert state["blocked"] == "input"
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
    assert state["sources"] == COVID_SOURCES
    assert state["sources_md"] == COVID_SOURCES_MD
    assert state["kg"] == [KG]


def test_predefined_links_join_the_sources(monkeypatch):
    linked = {"fisma": {"response": "FISMA canned answer.", "flag": "r", "title": "FISMA", "link": "https://x/fisma"},
              "covid": {"response": "Covid disclaimer.", "flag": "a", "title": "Covid", "link": "https://x/covid"},
              "flu": {"response": "Flu disclaimer.", "flag": "a", "title": "Flu", "link": ""}}
    monkeypatch.setitem(globals(), "PREDEFINED", linked)  # run() reads it
    # canned: llm calls guardrail "No", classifier "- fisma"
    state, _ = run(["No", "- fisma"])
    assert state["sources"] == {"predefined": [{"title": "FISMA", "link": "https://x/fisma", "type": "predefined"}]}
    assert state["sources_md"] == "**Sources**\n- [FISMA](https://x/fisma) (predefined)"
    # disclaimers: after the agent's sources; an empty link is listed without one
    state, _ = run(["No", "- covid\n- flu", "Yes", "- none"])
    assert state["sources"] == SOURCES | {"predefined": [{"title": "Covid", "link": "https://x/covid", "type": "predefined"},
                                                         {"title": "Flu", "link": "", "type": "predefined"}]}
    assert state["sources_md"] == SOURCES_MD + "\n- [Covid](https://x/covid) (predefined)\n- Flu (predefined)"
    # a rejected answer drops them with its disclaimers
    state, _ = run(["No", "- covid", "No"])
    assert state["sources"] == {} and state["sources_md"] == ""


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
    llm = StepLLM(["No", "- covid", "Yes", "- none"])
    graph = build_graph(llm, FakeAgent(), PREDEFINED)

    async def collect():
        return [e async for e in stream_chat(graph, "q", [])]

    events = [json.loads(line.removeprefix("data: ")) for line in asyncio.run(collect())]
    # every node start is announced, in workflow order; the guardrail and classifier start together,
    # and so do the output check and follow-ups; the covid disclaimer is appended after both
    nodes = [e["node"] for e in events if e["type"] == "node"]
    assert nodes[0] == "contextualize" and set(nodes[1:3]) == {"input_guardrail", "classify"}, nodes
    assert nodes[3] == "main_agent" and set(nodes[4:6]) == {"output_guardrail", "suggest_followups"}, nodes
    assert nodes[6:] == ["append_disclaimer"], nodes
    # the agent's tool call surfaces as a status event
    assert {"type": "status", "text": "calling search_docs"} in events
    # sources go out as soon as the agent node ends, before output_guardrail starts
    i_sources = events.index({"type": "sources", "sources": COVID_SOURCES, "sources_md": COVID_SOURCES_MD, "kg": [KG]})
    i_guard = events.index({"type": "node", "node": "output_guardrail"})
    assert i_sources < i_guard
    # tokens after the last reset are exactly the agent's final response —
    # earlier turns (tool-call preamble) get discarded by the reset
    last_reset = max(i for i, e in enumerate(events) if e["type"] == "reset")
    tokens = [e["text"] for e in events[last_reset:] if e["type"] == "token"]
    assert "".join(tokens) == "Agent answer about BDC."  # no guardrail/classifier chatter mixed in
    assert events[-1] == {"type": "done", "answer": "Agent answer about BDC.\n\nCovid disclaimer.",
                          "blocked": None, "topics": ["covid"], "followups": [],
                          "sources": COVID_SOURCES, "sources_md": COVID_SOURCES_MD, "kg": [KG], "mcp_errors": []}


def test_blocked_answer_gets_reject_reply_without_disclaimer():
    # llm calls: guardrail "No", classifier "- covid", output check "No" → REJECT, no append
    state, agent = run(["No", "- covid", "No"])
    assert agent.called
    assert state["blocked"] == "output"
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
    assert state["blocked"] == "output"
    assert state["answer"] == REJECT


def test_no_disclaimer_no_append_step():
    from r_assist.api import stream_chat

    # llm calls: guardrail "No", classifier "- none" (no "a" topic), output check "Yes", followups "- none"
    graph = build_graph(StepLLM(["No", "- none", "Yes", "- none"]), FakeAgent(), PREDEFINED)

    async def collect():
        return [json.loads(e.removeprefix("data: ")) async for e in stream_chat(graph, "q", [])]

    events = asyncio.run(collect())
    assert "append_disclaimer" not in [e.get("node") for e in events]
    assert events[-1]["answer"] == "Agent answer about BDC."


def test_stream_blocked_answer_replaced_in_done():
    from r_assist.api import stream_chat

    # llm calls: guardrail "No", classifier "- covid", output check "No", follow-ups (dropped with the answer)
    graph = build_graph(StepLLM(["No", "- covid", "No", "- Another question?"]), FakeAgent(), PREDEFINED)

    async def collect():
        return [json.loads(e.removeprefix("data: ")) async for e in stream_chat(graph, "q", [])]

    events = asyncio.run(collect())
    assert any(e["type"] == "token" for e in events)  # the provisional answer did stream
    # follow-ups ran alongside the output check; nothing else runs after the block
    assert {e["node"] for e in events[-3:-1]} == {"output_guardrail", "suggest_followups"}, events
    assert events[-1] == {"type": "done", "answer": REJECT, "blocked": "output", "topics": ["covid"],
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
    graph = build_graph(StepLLM(["No", "- none"]), FailingAgent(), PREDEFINED)

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
    llm = StepLLM(["No", "How do I get access to PIC-SURE?", "- other", "Yes", "- none"])
    monkeypatch.setattr(api, "graph", build_graph(llm, agent, PREDEFINED))
    monkeypatch.setattr(api, "mcp_errors", ["dug_mcp: ConnectError: x"])
    history = [{"role": "user", "content": "What is PIC-SURE?"}, {"role": "assistant", "content": "A BDC tool."}]

    res = TestClient(api.app).post("/chat", json={"input": "How do I get access to it?", "chat_history": history})
    assert res.status_code == 200, res.text
    assert agent.payload["messages"][0]["content"] == "How do I get access to PIC-SURE?"
    assert res.json() == {"answer": "Agent answer about BDC.", "blocked": None, "topics": [], "followups": [],
                          "sources": SOURCES, "sources_md": SOURCES_MD, "kg": [KG],
                          "mcp_errors": ["dug_mcp: ConnectError: x"]}


def test_many_users_at_once_run_in_parallel_and_never_mix(monkeypatch):
    """One server, many users: every step awaits the LLM or a tool, so concurrent /chat/stream
    requests overlap instead of queuing, and the one shared graph keeps each run's state its own."""
    import time

    import httpx

    from r_assist import api

    class SlowLLM:  # every model call takes 0.2s: input guardrail "No", the rest "Yes" (no topics, no followups)
        async def ainvoke(self, messages):
            await asyncio.sleep(0.2)
            return AIMessage(content="No" if messages[0] == ("system", prompts.INPUT_GUARDRAIL_SYSTEM) else "Yes")

    class EchoAgent:  # its tool calls take 0.2s too; answers name the question asked
        async def astream(self, payload, stream_mode=None):
            question = payload["messages"][0]["content"]
            await asyncio.sleep(0.2)
            yield "messages", (AIMessageChunk(content=f"answer to {question}", id=question), {})
            yield "values", {"messages": [AIMessage(content=f"answer to {question}")]}

    monkeypatch.setattr(api, "graph", build_graph(SlowLLM(), EchoAgent(), PREDEFINED))
    monkeypatch.setattr(api, "admission", api.Admission(0, 0))  # no cap, whatever .env sets

    async def user(client, n):
        res = await client.post("/chat/stream", json={"input": f"question {n}", "chat_history": []})
        events = [json.loads(line.removeprefix("data: ")) for line in res.text.split("\n\n") if line]
        return "".join(e["text"] for e in events if e["type"] == "token"), events[-1]["answer"]

    async def everyone():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://t") as client:
            await user(client, "warm-up")  # first-run imports must not count as waiting
            start = time.monotonic()
            results = await asyncio.gather(*(user(client, n) for n in range(20)))
            return results, time.monotonic() - start

    results, elapsed = asyncio.run(everyone())
    # one user alone takes ~1s (four model calls + the agent); queued, 20 would take ~20s
    assert elapsed < 2.5, f"20 users took {elapsed:.1f}s: requests ran one after another"
    assert results == [(f"answer to question {n}",) * 2 for n in range(20)], "a user got another user's answer"


def test_responses_api_content_blocks():
    """COMPLETION_REASONING_EFFORT above none puts the model on the Responses API, where message
    content is a list of blocks (reasoning, text, function_call), not a string. Guardrail verdicts,
    topics, the answer and the streamed tokens all come from the text blocks."""
    from r_assist.api import stream_chat

    replies = ["No", "- covid", "Yes", "- none"]  # input guardrail, classifier, output check, follow-ups
    llm = StepLLM(replies, wrap=_blocks)
    graph = build_graph(llm, FakeAgent(blocks=True), PREDEFINED)

    async def collect():
        return [json.loads(e.removeprefix("data: ")) async for e in stream_chat(graph, "q", [])]

    events = asyncio.run(collect())
    last_reset = max(i for i, e in enumerate(events) if e["type"] == "reset")
    assert "".join(e["text"] for e in events[last_reset:] if e["type"] == "token") == "Agent answer about BDC."
    done = events[-1]
    assert done["answer"] == "Agent answer about BDC.\n\nCovid disclaimer."
    assert (done["blocked"], done["topics"], done["followups"]) == (None, ["covid"], [])


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
    assert "prompt" not in servers["dug_mcp"], "r-assist's key, never handed to the transport"


def test_mcp_clients_share_one_ssl_context():
    """Each MCP tool call opens a fresh session, so a new HTTP client: by default httpx reloads the
    certificate bundle for every one (~95 ms with the event loop blocked)."""
    from pathlib import Path

    from r_assist.agent import load_mcp_servers

    servers = load_mcp_servers(Path("examples/bdc"))
    a, b = servers["r_doc_mcp"]["httpx_client_factory"](), servers["dug_mcp"]["httpx_client_factory"]()
    assert a._transport._pool._ssl_context is b._transport._pool._ssl_context


def test_keep_session_server(monkeypatch):
    """A keep_session server (a real stateless FastMCP one, in process) is called over one kept session:
    one request per call. A failed request closes that session without cancelling the task that opened
    it; the call retries on a fresh session, and recheck's rebuild opens a new kept one."""
    import httpx
    from mcp.server.fastmcp import FastMCP

    from r_assist import agent

    server = FastMCP("t", stateless_http=True, json_response=True)

    @server.tool()
    def echo(text: str) -> str:
        return text

    app = server.streamable_http_app()
    sent, down = [], {"once": False}

    async def hook(request):  # records each JSON-RPC method sent; fails one request when asked
        sent.append(json.loads(request.content).get("method") if request.method == "POST" else request.method)
        if down["once"]:
            down["once"] = False
            raise httpx.ConnectError("All connection attempts failed")

    def http_client(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8001",
                                 headers=headers, timeout=timeout, auth=auth, event_hooks={"request": [hook]})

    monkeypatch.setattr(agent, "_http_client", http_client)
    monkeypatch.setattr(agent, "_servers_yaml", lambda data_dir: {
        "docs": {"transport": "streamable_http", "url": "http://127.0.0.1:8001/mcp", "keep_session": True}})
    monkeypatch.setattr(agent, "create_deep_agent", lambda llm, tools, **kw: tools)

    async def scenario():
        async with server.session_manager.run():
            _, (tool,), errors = await agent.build_agent(None)
            assert errors == []
            sent.clear()
            results = await asyncio.gather(*(tool.ainvoke({"text": f"q{n}"}) for n in range(5)))
            assert [r[0]["text"] for r in results] == [f"q{n}" for n in range(5)], results
            assert sent == ["tools/call"] * 5, f"one request per call over the kept session, got {sent}"

            agent.recheck.clear()
            down["once"] = True  # the kept session's next request fails, which closes it
            assert (await tool.ainvoke({"text": "after"}))[0]["text"] == "after", "retried on a fresh session"
            await asyncio.sleep(0)
            assert agent._kept["docs"][1].done() and agent.recheck.is_set()

            _, (tool,), _ = await agent.build_agent(None)  # recheck's rebuild
            sent.clear()
            assert (await tool.ainvoke({"text": "again"}))[0]["text"] == "again"
            assert sent == ["tools/call"], sent

    try:
        # wait_for: a call left waiting on the closed session would hang the test instead of failing it
        asyncio.run(asyncio.wait_for(scenario(), 30))  # reaching the end: the closing session cancelled nothing here
    finally:
        agent.close_sessions()
        agent.recheck.clear()


def test_mcp_server_url_from_env(monkeypatch):
    from pathlib import Path

    from r_assist.agent import load_mcp_servers

    monkeypatch.setenv("R_DOC_MCP_URL", "http://doc-mcp:8001/mcp")
    servers = load_mcp_servers(Path("examples/bdc"))
    assert servers["r_doc_mcp"]["url"] == "http://doc-mcp:8001/mcp"
    assert servers["dug_mcp"]["url"] == "https://dug-mcp.apps.renci.org/sse", "no env var: the yaml's url"


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

    _, tools, errors = asyncio.run(agent.build_agent(None))
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
                   ("agent1", [], ["r_doc_mcp: ConnectError: x"]),  # went down mid-session
                   ("agent2", [], [])])                              # back

    async def build_agent(llm):
        result = next(builds, None)
        if result is None:
            raise asyncio.CancelledError  # shutdown
        recheck.set()  # each check sees another failed tool call: none may wait out the hour
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(api, "build_agent", build_agent)
    monkeypatch.setattr(api, "build_graph", lambda llm, agent, predefined, tools=(), nodes=(): agent)
    monkeypatch.setattr(api, "recheck", recheck)
    monkeypatch.setattr(api, "MCP_RETRY_SECONDS", 3600)
    monkeypatch.setattr(api, "graph", "agent0")
    monkeypatch.setattr(api, "mcp_errors", [])
    recheck.set()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(api.retry_mcp(None, {}, []))
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

    llm = StepLLM(["No", "- none", "Yes", "- none"])
    state = asyncio.run(build_graph(llm, CitingAgent(), PREDEFINED).ainvoke({"input": "q", "chat_history": []}))
    assert state["sources"] == {**SOURCES, "dug": [fhs, aric]}
    assert state["sources_md"] == (SOURCES_MD + "\n- [Framingham Cohort](https://x/study?phs000007) (dbgap-study)"
                                               "\n- [ARIC](https://x/study?phs000280) (dbgap-study)")


# related_work, an extra node from examples/bdc/nodes.py: papers on the question's topic from the related
# tool (BDC: artie-mcp's search_publications)
P1 = {"title": "Asthma paper", "link": "https://p/1", "type": "publication"}


@pytest.fixture
def related(monkeypatch):
    """The BDC config's extra nodes, loaded as api.py does, with stand-in prompts: load_nodes()'s entries."""
    monkeypatch.setattr(prompts, "DATA_DIR", Path(__file__).resolve().parent.parent / "examples" / "bdc")
    monkeypatch.setitem(prompts.PROJECT, "extra_nodes", {
        "after_agent": {"related_work": {"tool": "search_publications", "fields": ["related"]}}})
    monkeypatch.setitem(prompts.PROMPTS, "related_keywords_human", "Related terms for {input} / {answer}")
    monkeypatch.setitem(prompts.PROMPTS, "related_filter_human", "Papers that fit {input} / {answer}:\n{papers}")
    return load_nodes()


def paper_tool(found, raises=None, delay=0):
    """A stand-in search_publications: (tool, the kwargs of each call). Like an MCP tool, it returns
    (text, artifact) with the papers as structured content "sources"."""
    from langchain_core.tools import StructuredTool

    calls = []

    async def search(**kwargs):
        calls.append(kwargs)
        await asyncio.sleep(delay)
        if raises:
            raise raises
        return json.dumps({"total": len(found)}), {"structured_content": {"sources": {"artie": found}}}

    tool = StructuredTool.from_function(
        coroutine=search, name="search_publications", description="d", response_format="content_and_artifact",
        args_schema={"type": "object", "properties": {"keywords": {"type": "array", "items": {"type": "string"}}}})
    return tool, calls


def invoke(replies, tools=(), nodes=()):
    graph = build_graph(StepLLM(replies), FakeAgent(), PREDEFINED, tools=tools, nodes=nodes)
    return asyncio.run(graph.ainvoke({"input": "q", "chat_history": []}))


def test_related_papers_are_their_own_field_not_sources(related, caplog):
    # replies: guardrail, classify, output check, follow-ups, related terms, the papers that fit
    state, llm, calls = run_related(["No", "- none", "Yes", "- none", "- asthma\n- microbiome", "- 1\n- 2"],
                                    [P1, {"title": "FAQ title", "link": "https://x/faq", "type": "publication"}], related)
    assert calls == [{"keywords": ["asthma", "microbiome"]}]
    assert state["related"] == {"artie": [P1]}
    assert "FAQ title" not in llm.seen["filter"]  # https://x/faq is one of the answer's sources: not related reading
    assert state["sources"] == SOURCES and state["sources_md"] == SOURCES_MD  # the answer isn't based on them
    assert "skipped" not in caplog.text  # a node that fails is only logged: a passing test must check


def test_related_terms_capped_and_none_skips(related, caplog):
    tool, calls = paper_tool([P1])
    invoke(["No", "- none", "Yes", "- none", "- a\n- b\n- c\n- d"], tools=[tool], nodes=related)
    assert calls == [{"keywords": ["a", "b", "c"]}]  # the prompt asks for 1 to 3
    tool, calls = paper_tool([P1])
    state = invoke(["No", "- none", "Yes", "- none", "- none"], tools=[tool], nodes=related)
    assert calls == [] and state["sources"] == SOURCES
    assert state.get("related") is None and "skipped" not in caplog.text  # no search, no papers, nothing logged


def test_rejected_answer_drops_related_papers(related):
    state, llm, _ = run_related(["No", "- none", "No", "- none", "- asthma", "- 1"], [P1], related)
    assert "filter" in llm.seen  # it found and kept one, beside the rejected answer
    assert state["blocked"] == "output" and state["sources"] == {} and state["related"] is None


def test_related_work_failure_keeps_the_answer(related):
    from r_assist.agent import _stand_in

    # the tool raising, and artie-mcp down (its stand-in: no related_work step at all)
    for tool in (paper_tool([], raises=ConnectionError("down"))[0], _stand_in("search_publications", "artie_mcp")):
        state = invoke(["No", "- none", "Yes", "- none", "- asthma"], tools=[tool], nodes=related)
        assert state["answer"] == "Agent answer about BDC." and state["sources"] == SOURCES


def test_no_related_node_without_its_tool(related):
    tool, _ = paper_tool([])
    assert "related_work" not in build_graph(StepLLM([]), FakeAgent(), PREDEFINED, nodes=related).nodes
    assert "related_work" in build_graph(StepLLM([]), FakeAgent(), PREDEFINED, tools=[tool], nodes=related).nodes
    # no extra nodes (the default config names none): no related_work step, its tool or not
    assert "related_work" not in build_graph(StepLLM([]), FakeAgent(), PREDEFINED, tools=[tool]).nodes


def test_no_extra_nodes_named_none_loaded(monkeypatch):
    monkeypatch.delitem(prompts.PROJECT, "extra_nodes", raising=False)
    assert load_nodes() == {}


def test_extra_node_missing_from_nodes_py_fails(related, monkeypatch):
    monkeypatch.setitem(prompts.PROJECT, "extra_nodes", {"after_agent": {"related_work": None}, "after_finish": {"nope": None}})
    with pytest.raises(ValueError, match="no function nope"):
        load_nodes()


def test_extra_nodes_go_in_known_slots(related, monkeypatch):
    # no slot; a slot that doesn't exist; a list of names, not each node with its settings
    for bad in (["related_work"], {"after_guardrail": {"related_work": None}}, {"after_agent": ["related_work"]}):
        monkeypatch.setitem(prompts.PROJECT, "extra_nodes", bad)
        with pytest.raises(ValueError, match="before_agent, after_agent, after_finish"):
            load_nodes()
    for settings in ("search_publications", {"fields": "user_lang"}):  # not a mapping; fields not a list
        monkeypatch.setitem(prompts.PROJECT, "extra_nodes", {"after_agent": {"related_work": settings}})
        with pytest.raises(ValueError, match=r"extra_nodes\.after_agent\.related_work"):
            load_nodes()


def test_related_work_needs_its_prompts(related, monkeypatch):
    for key in ("related_keywords_human", "related_filter_human"):
        with monkeypatch.context() as m:
            m.delitem(prompts.PROMPTS, key)
            with pytest.raises(ValueError, match=key):
                build_graph(StepLLM([]), FakeAgent(), PREDEFINED, nodes=related)


def test_slow_related_tool_does_not_hold_up_the_answer(related, monkeypatch):
    # e.g. the artie API hanging: finish (and so "done") must not wait on an extra
    monkeypatch.setitem(related["after_agent"][0][1].__globals__, "RELATED_TIMEOUT", 0.2)
    tool, _ = paper_tool([P1], delay=3)
    start = time.monotonic()
    state = invoke(["No", "- none", "Yes", "- none", "- asthma"], tools=[tool], nodes=related)
    assert time.monotonic() - start < 2 and state["sources"] == SOURCES


def test_related_papers_capped_at_three(related):
    # even when more fit, at most 3 go under the answer
    papers = [{"title": f"P{n}", "link": f"https://p/{n}", "type": "publication"} for n in range(5)]
    tool, _ = paper_tool(papers)
    state = invoke(["No", "- none", "Yes", "- none", "- asthma", "- 1\n- 2\n- 3\n- 4\n- 5"], tools=[tool], nodes=related)
    assert state["related"]["artie"] == papers[:3]


def test_drawing_shows_the_agent_inside():
    from deepagents import create_deep_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    from r_assist.graph import drawing

    # the agent runs inside the agent node's function, so get_graph(xray=True) can't see it
    agent = create_deep_agent(GenericFakeChatModel(messages=iter([])), [], system_prompt="")
    m = drawing(build_graph(None, agent, PREDEFINED), agent,
                {"r_doc_mcp": {}, "dug_mcp": {"interceptors": ["dug_kg"]}})
    # the agent is a box in the main flow: edges go to and from the box, not into its steps
    assert "subgraph main_agent" in m and ".-> main_agent;" in m and "main_agent --> output_guardrail;" in m
    # inside: model and tools calling each other, and the servers the tools call
    assert r"main_agent\3amodel -.-> main_agent\3atools;" in m and r"main_agent\3atools -.-> main_agent\3amodel;" in m
    assert r"main_agent\3atools -.-> main_agent\3ar_doc_mcp;" in m and "(dug_mcp + dug_kg)" in m
    # deepagents' middleware hooks and the model's self-loop are internals, left out
    assert "Middleware" not in m and r"main_agent\3amodel -.-> main_agent\3amodel;" not in m
    assert "__end__([<p>__end__</p>]):::last" in m  # still styled as the end
    # both guardrails' exits say "blocked", as the state's blocked field does
    assert "&nbsp;blocked or canned&nbsp;" in m and "&nbsp;done or blocked&nbsp;" in m


def test_related_terms_lose_quotes_and_dots(related):
    tool, calls = paper_tool([P1])
    invoke(["No", "- none", "Yes", "- none", '- "asthma"\n- none.'], tools=[tool], nodes=related)
    assert calls == [{"keywords": ["asthma"]}]


def test_prose_or_empty_reply_skips_search(related):
    for reply in ("Asthma is a lung disease.", ""):
        tool, calls = paper_tool([P1])
        state = invoke(["No", "- none", "Yes", "- none", reply], tools=[tool], nodes=related)
        assert calls == [] and state["sources"] == SOURCES


def test_related_work_failure_is_logged(related, caplog):
    tool, _ = paper_tool([], raises=ConnectionError("artie down"))
    invoke(["No", "- none", "Yes", "- none", "- asthma"], tools=[tool], nodes=related)
    assert "related_work" in caplog.text and "ConnectionError: artie down" in caplog.text


def test_no_related_node_for_a_stand_in(related):
    from r_assist.agent import _stand_in

    # artie-mcp down at startup: its stand-in only says so, not worth an LLM call per chat
    tool = _stand_in("search_publications", "artie_mcp")
    assert "related_work" not in build_graph(StepLLM([]), FakeAgent(), PREDEFINED, tools=[tool], nodes=related).nodes


def test_missing_related_tool_is_logged(related, caplog):
    build_graph(StepLLM([]), FakeAgent(), PREDEFINED, tools=[], nodes=related)
    assert "related_work's tool 'search_publications'" in caplog.text


P2 = {"title": "Sleep paper", "link": "https://p/2", "type": "publication"}
P3 = {"title": "COPD paper", "link": "https://p/3", "type": "publication"}


def run_related(replies, found, nodes):
    """related_work with its LLM at hand: (final state, the fake LLM, the tool's calls)."""
    llm, (tool, calls) = StepLLM(replies), paper_tool(found)
    graph = build_graph(llm, FakeAgent(), PREDEFINED, tools=[tool], nodes=nodes)
    state = asyncio.run(graph.ainvoke({"input": "q", "chat_history": []}))
    return state, llm, calls


def test_only_papers_that_fit_are_shown(related):
    # the search ORs every word, so some results share only a word or a program: the LLM picks, best first
    state, llm, _ = run_related(["No", "- none", "Yes", "- none", "- asthma", "- 3\n- 1"], [P1, P2, P3], related)
    assert state["related"]["artie"] == [P3, P1]
    assert "1. Asthma paper\n2. Sleep paper\n3. COPD paper" in llm.seen["filter"]  # numbered titles to pick from


def test_no_paper_that_fits_shows_none(related):
    for reply in ("- none", "- 7\n- the first one", ""):  # none, numbers it wasn't given or no numbers, no reply
        state, _, _ = run_related(["No", "- none", "Yes", "- none", "- asthma", reply], [P1, P2], related)
        assert state["sources"] == SOURCES and state.get("related") is None, reply


def test_no_fit_check_when_the_search_finds_nothing(related, caplog):
    state, llm, calls = run_related(["No", "- none", "Yes", "- none", "- asthma"], [], related)
    assert calls and "filter" not in llm.seen and state["sources"] == SOURCES
    assert "skipped" not in caplog.text


# the config's own nodes in the in-order slots, and the optional built-in steps
def slots(**factories):
    """build_graph's nodes for factories without settings, each named after its function."""
    return {slot: [(f.__name__, f, {}) for f in fs] for slot, fs in factories.items()}


def chat(llm_replies, agent=None, **graph_args):
    graph = build_graph(StepLLM(llm_replies), agent or FakeAgent(), PREDEFINED, **graph_args)
    return asyncio.run(graph.ainvoke({"input": "q", "chat_history": []})), graph


def expand(llm, tools, settings):
    async def node(state):
        return {"question": state["question"] + " (expanded)"}
    return node


def tag(llm, tools, settings):
    async def node(state):
        return {"question": state["question"] + " (tagged)"}
    return node


def test_before_agent_nodes_run_in_order_before_the_agent():
    agent = FakeAgent()
    chat(["No", "- none", "Yes", "- none"], agent, nodes=slots(before_agent=[expand, tag]))
    assert agent.payload["messages"][0]["content"] == "q (expanded) (tagged)"
    # a refused question ends at gate, before them
    state, _ = chat(["Yes"], nodes=slots(before_agent=[expand]))
    assert state["answer"] == REFUSAL and state["question"] == "q"


def test_a_before_agent_node_s_answer_ends_the_chat():
    def screen(llm, tools, settings):
        async def node(state):
            return {"answer": "Ask the help desk."}
        return node

    agent = FakeAgent()
    state, graph = chat(["No", "- covid"], agent, nodes=slots(before_agent=[screen, tag]))
    assert state["answer"] == "Ask the help desk." and not agent.called
    assert state["question"] == "q"  # tag, after it, didn't run
    # no follow-ups, nor the covid disclaimer or its source: those go with the agent's answer
    assert not state.get("followups") and state["sources"] == {} and state["sources_md"] == ""
    assert "&nbsp;answered&nbsp;" in graph.get_graph().draw_mermaid()


def test_node_events_carry_their_labels(monkeypatch):
    import json

    from r_assist.api import stream_chat

    monkeypatch.setitem(prompts.PROJECT, "builtin_nodes", {"main_agent": {"label": "Looking it up…"}})
    graph = build_graph(StepLLM(["No", "- none", "Yes", "- none"]), FakeAgent(), PREDEFINED,
                        nodes={"before_agent": [("expand", expand, {"label": "Widening the question…"})]})

    async def collect():
        return [json.loads(e.removeprefix("data: ")) for e in [e async for e in stream_chat(graph, "q", [])]]

    nodes = [e for e in asyncio.run(collect()) if e["type"] == "node"]
    assert {"type": "node", "node": "expand", "label": "Widening the question…"} in nodes
    assert {"type": "node", "node": "main_agent", "label": "Looking it up…"} in nodes  # relabeled
    assert {"type": "node", "node": "output_guardrail"} in nodes  # not set: the client's own text
    # a step that isn't built in (or isn't announced); a setting that isn't label; not a mapping
    for bad in ({"gate": {"label": "x"}}, {"main_agent": {"timeout": 5}}, {"main_agent": "Thinking"}):
        monkeypatch.setitem(prompts.PROJECT, "builtin_nodes", bad)
        with pytest.raises(ValueError, match="builtin_nodes"):
            build_graph(StepLLM([]), FakeAgent(), PREDEFINED)


def test_extra_fields_pass_between_nodes_and_come_back_with_the_answer(monkeypatch, caplog):
    from fastapi.testclient import TestClient

    from r_assist import api

    def detect(llm, tools, settings):  # before_agent
        async def node(state):
            return {"user_lang": "es", "mood": "calm"}  # mood isn't listed
        return node

    def count(llm, tools, settings):  # after_agent: writes its own field, not detect's
        async def node(state):
            return {"words": len(state["answer"].split()), "user_lang": "fr"}
        return node

    def reply_in(llm, tools, settings):  # after_finish reads it
        async def node(state):
            return {"answer": f"[{state['user_lang']}] {state['answer']}"}
        return node

    # each node lists its fields: one of its own, and one of the state's the response leaves out
    nodes = {"before_agent": [("detect", detect, {"fields": ["user_lang"]})],
             "after_agent": [("count", count, {"fields": ["words"]})],
             "after_finish": [("reply_in", reply_in, {"fields": ["question", "never"]})]}
    graph = build_graph(StepLLM(["No", "- none", "Yes", "- none"]), FakeAgent(), PREDEFINED, nodes=nodes)
    monkeypatch.setattr(api, "graph", graph)
    monkeypatch.setattr(api, "extra_fields", api.check_extra_fields(node_fields(nodes)))
    res = TestClient(api.app).post("/chat", json={"input": "q", "chat_history": []}).json()
    assert res["answer"] == "[es] Agent answer about BDC." and res["user_lang"] == "es" and res["question"] == "q"
    assert res["never"] is None  # listed, but no node set it: null, not a stand-in that breaks the JSON
    assert res["words"] == 4 and "count: dropped user_lang (an after_agent node returns only the fields it lists)" in caplog.text
    assert "mood" not in res and "detect: dropped mood (not a state field" in caplog.text
    for bad in (["answer"], ["user_lang", "mcp_errors"]):  # already in the response
        with pytest.raises(ValueError, match="the response's own"):
            api.check_extra_fields(bad)


def shout(llm, tools, settings):
    async def node(state):
        return {"answer": state["answer"].upper()}
    return node


def broken(llm, tools, settings):
    async def node(state):
        raise RuntimeError("bad")
    return node


def test_after_finish_nodes_reshape_a_passed_answer_before_its_disclaimers(caplog):
    nodes = slots(after_finish=[shout, broken])
    state, graph = chat(["No", "- covid", "Yes", "- none"], nodes=nodes)
    assert state["answer"] == "AGENT ANSWER ABOUT BDC.\n\nCovid disclaimer."
    assert "broken skipped: RuntimeError: bad" in caplog.text  # logged, and the answer kept
    assert "&nbsp;passed&nbsp;" in graph.get_graph().draw_mermaid()  # every branch has its edge drawn
    # a rejected answer stays the canned reply, untouched
    state, _ = chat(["No", "- none", "No"], nodes=nodes)
    assert state["answer"] == REJECT


def test_followups_and_disclaimers_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(prompts, "FOLLOWUPS", 0)
    monkeypatch.setattr(prompts, "APPEND_DISCLAIMER", False)
    state, graph = chat(["No", "- covid", "Yes", "- What is BDC?"], nodes=slots(after_finish=[shout]))
    assert "suggest_followups" not in graph.nodes and "append_disclaimer" not in graph.nodes
    assert state["answer"] == "AGENT ANSWER ABOUT BDC." and not state.get("followups")
    assert state["disclaimers"] == ["Covid disclaimer."]  # left for a node of the config's own


def test_after_agent_node_cannot_clash_with_the_steps_beside_it(caplog):
    # followups is suggest_followups' to write: kept as is, it would fail the whole chat
    def greedy(llm, tools, settings):
        async def node(state):
            return {"followups": ["mine"], "mine": 1}
        return node

    state, _ = chat(["No", "- none", "Yes", "- What is BDC?"], nodes={"after_agent": [("greedy", greedy, {"fields": ["mine"]})]})
    assert state["followups"] == ["What is BDC?"] and state["mine"] == 1  # its own field kept
    assert "greedy: dropped followups" in caplog.text


def test_a_slow_node_is_skipped_by_its_slot_s_timeout(monkeypatch, caplog):
    # each slot has its own limit: a node over its slot's is skipped, the answer comes anyway
    monkeypatch.setattr(prompts, "NODE_TIMEOUT", {"before_agent": 0.2, "after_finish": 2})

    def hang(llm, tools, settings):
        async def node(state):
            await asyncio.sleep(5)
            return {"question": "never"}
        return node

    def slow_shout(llm, tools, settings):
        async def node(state):
            await asyncio.sleep(0.5)  # over before_agent's limit, within after_finish's
            return {"answer": state["answer"].upper()}
        return node

    start = time.monotonic()
    agent = FakeAgent()
    state, _ = chat(["No", "- none", "Yes", "- none"], agent,
                    nodes=slots(before_agent=[hang], after_finish=[slow_shout]))
    assert time.monotonic() - start < 2 and state["answer"] == "AGENT ANSWER ABOUT BDC."
    assert agent.payload["messages"][0]["content"] == "q"
    assert "hang skipped: TimeoutError" in caplog.text and "slow_shout skipped" not in caplog.text


def test_node_timeout_goes_in_known_slots(monkeypatch):
    for bad in (5, {"after_guardrail": 5}):  # not per slot; a slot that doesn't exist
        monkeypatch.setattr(prompts, "NODE_TIMEOUT", bad)
        with pytest.raises(ValueError, match="node_timeout"):
            load_nodes()


# what breaks or comes back empty: the LLM, MCP servers, tools, the config's own nodes
class DeadLLM(StepLLM):
    """StepLLM whose provider is down at one workflow step."""

    def __init__(self, replies, dead):
        super().__init__(replies)
        self.dead = dead

    async def ainvoke(self, messages):
        if self.step(messages) == self.dead:
            raise ConnectionError("LLM gateway down")
        return await super().ainvoke(messages)


@pytest.mark.parametrize("dead", ["guardrail", "contextualize", "classify", "output"])
def test_a_dead_llm_ends_the_stream_with_an_error(dead, caplog):
    """No step the answer depends on can do without the LLM: the stream ends with an error event
    (the client says so) instead of hanging or cutting off."""
    from r_assist.api import stream_chat

    # with history, so contextualize calls the LLM too; replies in workflow order
    graph = build_graph(DeadLLM(["No", "q", "- none", "Yes", "- none"], dead), FakeAgent(), PREDEFINED)

    async def collect():
        return [json.loads(e.removeprefix("data: "))
                async for e in stream_chat(graph, "q", [("user", "hi"), ("assistant", "hello")])]

    events = asyncio.run(collect())
    assert events[-1] == {"type": "error"} and not any(e["type"] == "done" for e in events), events
    assert "LLM gateway down" in caplog.text


def test_a_dead_llm_costs_only_the_follow_ups_and_config_nodes(caplog):
    # the decorative steps: the answer comes anyway
    def ask(llm, tools, settings):  # a config node calling the LLM
        async def node(state):
            await llm.ainvoke([("human", "Rate: " + state["answer"])])
            return {"answer": "never"}
        return node

    graph = build_graph(DeadLLM(["No", "- none", "Yes"], "followups"), FakeAgent(), PREDEFINED,
                        nodes=slots(after_finish=[ask]))
    state = asyncio.run(graph.ainvoke({"input": "q", "chat_history": []}))
    assert state["answer"] == "Agent answer about BDC." and state["followups"] == []
    assert "ask skipped: ConnectionError: LLM gateway down" in caplog.text


def test_config_nodes_that_return_nothing_change_nothing(caplog):
    def nothing(llm, tools, settings):
        async def node(state):
            return None
        return node

    nodes = {slot: [(f"nothing_{slot}", nothing, {})] for slot in ("before_agent", "after_agent", "after_finish")}
    state, _ = chat(["No", "- covid", "Yes", "- none"], nodes=nodes)
    assert state["answer"] == "Agent answer about BDC.\n\nCovid disclaimer." and state["sources"] == COVID_SOURCES
    assert "nothing_" not in caplog.text  # neither skipped nor dropped


def test_startup_reports_a_dead_mcp_server_and_picks_it_up_when_back(monkeypatch):
    """Through the app's own startup: a server down at startup is in /health while chats go on
    without its tools, the config nodes' fields come back with each answer, and the timer's
    re-check brings the server back without a restart."""
    from fastapi.testclient import TestClient

    from r_assist import api

    def detect(llm, tools, settings):
        async def node(state):
            return {"user_lang": "es"}
        return node

    down = "dug_mcp: ConnectError: All connection attempts failed"
    builds = [(FakeAgent(), [], [down]), (FakeAgent(), [], [])]  # at startup; at every re-check

    async def build_agent(llm):
        return builds.pop(0) if len(builds) > 1 else builds[0]

    for name, value in [("graph", None), ("mcp_errors", []), ("extra_fields", [])]:
        monkeypatch.setattr(api, name, value)  # startup sets them: put back after
    monkeypatch.setattr(api, "get_llm", lambda: StepLLM(["No", "- none", "Yes", "- none"]))
    monkeypatch.setattr(api, "load_predefined_responses", lambda: PREDEFINED)
    monkeypatch.setattr(api, "load_nodes", lambda: {"before_agent": [("detect", detect, {"fields": ["user_lang"]})]})
    monkeypatch.setattr(api, "build_agent", build_agent)
    monkeypatch.setattr(api, "recheck", asyncio.Event())  # one of this app's loop
    monkeypatch.setattr(api, "MCP_RETRY_SECONDS", 1)
    with TestClient(api.app) as client:
        assert client.get("/health").json()["mcp_errors"] == [down]
        res = client.post("/chat", json={"input": "q", "chat_history": []}).json()
        assert res["answer"] == "Agent answer about BDC." and res["user_lang"] == "es"
        deadline = time.monotonic() + 10
        while client.get("/health").json()["mcp_errors"] and time.monotonic() < deadline:
            time.sleep(0.1)
        assert client.get("/health").json()["mcp_errors"] == []


def test_a_server_that_fails_as_its_kept_session_opens_is_skipped(monkeypatch):
    """A keep_session server that lists its tools, then fails as its kept session opens (it went
    down in between): reported like any unreachable server, its stand-ins in its tools' place."""
    import httpx
    from mcp.server.fastmcp import FastMCP

    from r_assist import agent

    server = FastMCP("t", stateless_http=True, json_response=True)

    @server.tool()
    def echo(text: str) -> str:
        return text

    app = server.streamable_http_app()
    opens = []

    async def hook(request):  # the tool listing's session opens; the kept one's doesn't
        if request.method == "POST" and json.loads(request.content).get("method") == "initialize":
            opens.append(1)
            if len(opens) > 1:
                raise httpx.ConnectError("All connection attempts failed")

    def http_client(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8001",
                                 headers=headers, timeout=timeout, auth=auth, event_hooks={"request": [hook]})

    monkeypatch.setattr(agent, "_http_client", http_client)
    monkeypatch.setattr(agent, "_servers_yaml", lambda data_dir: {"docs": {
        "transport": "streamable_http", "url": "http://127.0.0.1:8001/mcp", "keep_session": True,
        "stand_ins": ["echo"]}})
    monkeypatch.setattr(agent, "create_deep_agent", lambda llm, tools, **kw: tools)

    async def scenario():
        async with server.session_manager.run():
            return await agent.build_agent(None)

    try:
        _, tools, errors = asyncio.run(asyncio.wait_for(scenario(), 30))
    finally:
        agent.close_sessions()
    assert errors == ["docs: ConnectError: All connection attempts failed"], errors
    assert [t.name for t in tools] == ["echo"] and (tools[0].metadata or {}).get("stand_in")


def test_odd_tool_replies_are_read_as_far_as_they_go():
    from r_assist.agent import _error_text
    from r_assist.graph import _doc_sources, _doc_title, _parse_tool_content

    # an error reply cut short (no longer JSON) is still reported; an empty result is no error
    cut = '{"error": "upstream timed out", "detail": "...'
    assert _error_text(cut) == cut and _error_text('{"total": 0, "items": []}') is None
    # a doc search answering plain text (e.g. its server's own error message): no sources, no crash
    assert _parse_tool_content("search unavailable") == "search unavailable"
    msgs = [AIMessage(content="", tool_calls=[{"name": prompts.DOC_SEARCH_TOOL, "args": {}, "id": "t1"}]),
            ToolMessage(content="search unavailable", tool_call_id="t1")]
    assert _doc_sources(msgs) == []
    assert _doc_title({}, "https://x/docs/getting-started/") == "getting-started"  # no title, nor headings


def test_after_agent_nodes_cannot_list_one_field(tmp_path, monkeypatch):
    """after_agent nodes run side by side, with each other and with output_guardrail and
    suggest_followups: two writing one field would fail every chat, so startup fails instead."""
    (tmp_path / "nodes.py").write_text("def a(llm, tools, settings): pass\ndef b(llm, tools, settings): pass\n",
                                       encoding="utf-8")
    monkeypatch.setattr(prompts, "DATA_DIR", tmp_path)
    for after_agent, why in [({"a": {"fields": ["x"]}, "b": {"fields": ["x"]}}, "a lists it too"),
                             ({"a": {"fields": ["followups"]}}, "it is the workflow's own")]:
        monkeypatch.setitem(prompts.PROJECT, "extra_nodes", {"after_agent": after_agent})
        with pytest.raises(ValueError, match=why):
            load_nodes()
    # one field in two slots that don't run side by side is fine
    monkeypatch.setitem(prompts.PROJECT, "extra_nodes", {"after_agent": {"a": {"fields": ["x"]}},
                                                         "after_finish": {"b": {"fields": ["x"]}}})
    assert [n for n, _, _ in load_nodes()["after_finish"]] == ["b"]
