"""FastAPI wrapper: POST /chat runs the graph. Stateless — history comes from the client."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import prompts
from .agent import build_agent, recheck
from .config import CORS_ORIGINS, LOG_LEVEL, MCP_RETRY_SECONDS, get_llm
from .graph import build_graph
from .prompts import load_predefined_responses


class Message(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    input: str
    chat_history: list[Message] = []


class ChatResponse(BaseModel):
    answer: str
    blocked: str | None = None     # "input" (question refused) or "output" (answer rejected)
    topics: list[str] = []
    followups: list[str] = []
    sources: dict = {}             # {sources_key: [{title, link, type}]}, deduplicated (project.yaml)
    sources_md: str = ""           # the same as a markdown list
    kg: list = []                  # knowledge graphs attached to tool results, one per tool call
    mcp_errors: list[str] = []     # "server: error" per MCP server currently unavailable (retried)


log = logging.getLogger(__name__)

graph = None
mcp_errors: list[str] = []  # from build_agent: startup, then each re-check


async def retry_mcp(llm, predefined):
    """Rebuild the agent from whichever MCP servers answer, every MCP_RETRY_SECONDS or as soon as
    a tool call fails twice (recheck): a server that was down gets its tools back, one that went
    down mid-session is dropped and listed in mcp_errors, all without a restart. Each rebuild is
    swapped in, so graph and mcp_errors always agree; running requests keep the graph they had."""
    global graph, mcp_errors
    while True:
        try:
            await asyncio.wait_for(recheck.wait(), MCP_RETRY_SECONDS)
        except TimeoutError:
            pass
        recheck.clear()
        try:
            agent, errors = await build_agent(llm)
        except Exception:  # e.g. mcp_servers.yaml edited into a broken state: keep what runs
            log.exception("MCP reload failed, keeping the current tools")
            continue
        back = {e.split(":")[0] for e in mcp_errors} - {e.split(":")[0] for e in errors}
        if back:
            # warning, not info: the default LOG_LEVEL must show the "unavailable" lines end
            log.warning("MCP server back, its tools loaded: %s", ", ".join(sorted(back)))
        graph, mcp_errors = build_graph(llm, agent, predefined), errors


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph, mcp_errors
    llm, predefined = get_llm(), load_predefined_responses()
    agent, mcp_errors = await build_agent(llm)
    graph = build_graph(llm, agent, predefined)
    retry = asyncio.create_task(retry_mcp(llm, predefined))
    yield
    retry.cancel()


# app loggers (e.g. MCP tool failures in agent.py) print alongside uvicorn's own lines
logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s:     %(name)s: %(message)s")

app = FastAPI(title=prompts.PROJECT.get("assistant_name", "r-assist"), version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    return {"status": "ok", "mcp_errors": mcp_errors}


async def stream_chat(graph, input: str, chat_history: list):
    """SSE events, one JSON object each:
      {"type": "node", "node"}    a graph node started (progress)
      {"type": "status", "text"}  what the agent is doing (tool calls)
      {"type": "token", "text"}   one token of the agent's provisional answer
      {"type": "reset"}           new model turn — discard tokens so far
      {"type": "sources", sources, sources_md, kg}  the agent finished; its doc sources and graphs
      {"type": "done", answer, blocked, topics, followups, sources, sources_md, kg, mcp_errors}
        final state — the done answer is authoritative (blocks, disclaimers, canned replies).
      {"type": "error"}           the run failed (details in the server log); last event, no done
    Custom-stream events come only from graph.py nodes, so guardrail/classifier
    LLM chatter never leaks."""
    state = {}
    try:
        async for mode, chunk in graph.astream(
            {"input": input, "chat_history": chat_history},
            stream_mode=["custom", "values"],
        ):
            if mode == "values":
                state = chunk
            elif isinstance(chunk, dict):
                yield f"data: {json.dumps(chunk)}\n\n"
    except Exception:
        # an LLM/gateway error or recursion limit mid-answer: say so, rather than cut the stream,
        # which a browser reports as a network error
        log.exception("chat run failed")
        yield f"data: {json.dumps({'type': 'error'})}\n\n"
        return
    done = {"type": "done", "answer": state.get("answer", ""),
            "blocked": state.get("blocked"),
            "topics": state.get("topics", []), "followups": state.get("followups", []),
            "sources": state.get("sources", {}), "sources_md": state.get("sources_md", ""),
            "kg": state.get("kg", []), "mcp_errors": mcp_errors}
    yield f"data: {json.dumps(done)}\n\n"


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    history = [(m.role, m.content) for m in req.chat_history]
    return StreamingResponse(stream_chat(graph, req.input, history),
                             media_type="text/event-stream")


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    state = await graph.ainvoke({
        "input": req.input,
        "chat_history": [(m.role, m.content) for m in req.chat_history],
    })
    return ChatResponse(answer=state["answer"], blocked=state.get("blocked"),
                        topics=state.get("topics", []), followups=state.get("followups", []),
                        sources=state.get("sources", {}), sources_md=state.get("sources_md", ""),
                        kg=state.get("kg", []), mcp_errors=mcp_errors)
