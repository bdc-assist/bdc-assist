"""FastAPI wrapper: POST /chat runs the graph. Stateless — history comes from the client."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .agent import build_agent
from .config import get_llm
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


graph = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph
    llm = get_llm()
    graph = build_graph(llm, await build_agent(llm), load_predefined_responses())
    yield


app = FastAPI(title="BDC Assist", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    state = await graph.ainvoke({
        "input": req.input,
        "chat_history": [(m.role, m.content) for m in req.chat_history],
    })
    return ChatResponse(answer=state["answer"], blocked=state.get("blocked", False),
                        topics=state.get("topics", []))
