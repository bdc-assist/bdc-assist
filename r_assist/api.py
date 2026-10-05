"""FastAPI wrapper: POST /chat runs the graph. Stateless — history comes from the client."""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import prompts
from .agent import build_agent
from .config import CORS_ORIGINS, LOG_LEVEL, get_llm
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
    blocked: bool = False
    topics: list[str] = []
    followups: list[str] = []
    sources: dict = {}             # {sources_key: [{title, link, type}]}, deduplicated (project.yaml)
    sources_md: str = ""           # the same as a markdown list
    kg: list = []                  # knowledge graphs attached to tool results, one per tool call


graph = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph
    llm = get_llm()
    graph = build_graph(llm, await build_agent(llm), load_predefined_responses())
    yield


# app loggers (e.g. MCP tool failures in agent.py) print alongside uvicorn's own lines
logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s:     %(name)s: %(message)s")

app = FastAPI(title=prompts.PROJECT.get("assistant_name", "r-assist"), version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    return {"status": "ok"}


async def stream_chat(graph, input: str, chat_history: list):
    """SSE events, one JSON object each:
      {"type": "node", "node"}    a graph node started (progress)
      {"type": "status", "text"}  what the agent is doing (tool calls)
      {"type": "token", "text"}   one token of the agent's provisional answer
      {"type": "reset"}           new model turn — discard tokens so far
      {"type": "sources", sources, sources_md, kg}  the agent finished; its doc sources and graphs
      {"type": "done", answer, blocked, topics, followups, sources, sources_md, kg}
        final state — the done answer is authoritative (rejects, disclaimers, canned replies).
    Custom-stream events come only from graph.py nodes, so guardrail/classifier
    LLM chatter never leaks."""
    state = {}
    async for mode, chunk in graph.astream(
        {"input": input, "chat_history": chat_history},
        stream_mode=["custom", "values"],
    ):
        if mode == "values":
            state = chunk
        elif isinstance(chunk, dict):
            yield f"data: {json.dumps(chunk)}\n\n"
    done = {"type": "done", "answer": state.get("answer", ""),
            "blocked": state.get("blocked", False),
            "topics": state.get("topics", []), "followups": state.get("followups", []),
            "sources": state.get("sources", {}), "sources_md": state.get("sources_md", ""),
            "kg": state.get("kg", [])}
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
    return ChatResponse(answer=state["answer"], blocked=state.get("blocked", False),
                        topics=state.get("topics", []), followups=state.get("followups", []),
                        sources=state.get("sources", {}), sources_md=state.get("sources_md", ""),
                        kg=state.get("kg", []))
