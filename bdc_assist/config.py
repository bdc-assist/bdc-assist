"""Env-driven model setup — same var names as bdc_doc_mcp. No GUARDIAN_MODEL."""

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()  # real env vars win over .env

# the doc MCP server is a separate service (bdc-doc-mcp repo) — bdc-assist only connects
DOC_RAG_MCP_URL = os.getenv("DOC_RAG_MCP_URL", "http://127.0.0.1:8001/mcp")


def _self_hosted_key():
    # self-hosted vLLM ignores the key, but the openai client refuses to start without one
    return os.getenv("OPENAI_API_KEY") or "EMPTY"


def _provider(kind: str) -> str:
    explicit = os.getenv(f"{kind}_MODEL_PROVIDER")
    if explicit:
        # split on '#': a trailing .env comment can survive into the value and is unreadable as an error
        return explicit.split("#")[0].strip().lower()
    url = os.getenv(f"{kind}_URL")
    if url:
        if "ollama" in url:
            return "ollama"
        return "azure" if "azure.com" in url else "vllm"
    return "openai"


@lru_cache
def get_llm():
    provider = _provider("COMPLETION")
    url = os.getenv("COMPLETION_URL")
    model = os.getenv("COMPLETION_MODEL")
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model or "gpt-4o-mini", temperature=0)
    if provider == "azure":
        # COMPLETION_MODEL is the *deployment* name here, and COMPLETION_URL the resource root
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(
            azure_endpoint=url, azure_deployment=model, temperature=0,
            api_version=os.getenv("AZURE_API_VERSION", "2024-10-21"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"),
        )
    if provider == "vllm":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(base_url=url, model=model, temperature=0, api_key=_self_hosted_key())
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(base_url=url, model=model, temperature=0)
    raise ValueError(f"Unsupported COMPLETION_MODEL_PROVIDER: {provider}")


@lru_cache
def get_emb():
    # not used by the bot yet; kept so .env carries llm + embedding config per spec
    provider = _provider("EMBEDDING")
    url = os.getenv("EMBEDDING_URL")
    model = os.getenv("EMBEDDING_MODEL")
    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(model=model or "text-embedding-3-small", check_embedding_ctx_length=False)
    if provider == "vllm":
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(base_url=url, model=model, api_key=_self_hosted_key(),
                                check_embedding_ctx_length=False)
    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings
        return OllamaEmbeddings(base_url=url, model=model)
    raise ValueError(f"Unsupported EMBEDDING_MODEL_PROVIDER: {provider}")
