"""Env-driven model setup — same var names as r_doc_mcp. No GUARDIAN_MODEL."""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # real env vars win over .env

# browser origins allowed to call the API, comma-separated; "*" = any (demo default)
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

COMPLETION_TEMPERATURE = float(os.getenv("COMPLETION_TEMPERATURE", "0"))  # every get_llm() provider
# unset for non-reasoning models (gpt-4o, gpt-4o-mini); none|low|medium|high for reasoning ones
# (gpt-6-luna). See _openai_kwargs. openai/azure/vllm only.
COMPLETION_REASONING_EFFORT = os.getenv("COMPLETION_REASONING_EFFORT") or None
LOG_LEVEL = os.getenv("LOG_LEVEL", "WARNING")  # api.py's root log level: DEBUG|INFO|WARNING|ERROR
# seconds between MCP server re-checks (api.retry_mcp): a down server's tools come back, one gone
# down mid-session drops out. A tool call that fails twice re-checks at once.
MCP_RETRY_SECONDS = float(os.getenv("MCP_RETRY_SECONDS", "300"))

# folder with the yaml files (project, prompts, predefined_responses, mcp_servers); examples/bdc runs the BDC bot
CONFIG_DIR = Path(os.getenv("CONFIG_DIR") or Path(__file__).resolve().parent.parent / "config")


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


def _openai_kwargs() -> dict:
    """Temperature and reasoning for the OpenAI-family clients, by COMPLETION_REASONING_EFFORT.
    Each rule is what the API returned for that model (tests/test_config.py):
      unset   temperature only: gpt-4o-mini rejects any reasoning_effort, even "none"
      none    reasoning off: gpt-6-luna then takes COMPLETION_TEMPERATURE and calls tools on chat completions
      other   no temperature (a reasoning gpt-6-luna takes only its default) and the Responses API, the
              only one where it calls tools while reasoning; its replies are content blocks (graph.py
              reads .text)."""
    if not COMPLETION_REASONING_EFFORT:
        return {"temperature": COMPLETION_TEMPERATURE}
    if COMPLETION_REASONING_EFFORT == "none":
        return {"temperature": COMPLETION_TEMPERATURE, "reasoning_effort": "none"}
    return {"reasoning_effort": COMPLETION_REASONING_EFFORT, "use_responses_api": True}


@lru_cache
def get_llm():
    provider = _provider("COMPLETION")
    url = os.getenv("COMPLETION_URL")
    model = os.getenv("COMPLETION_MODEL")
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model or "gpt-4o-mini", **_openai_kwargs())
    if provider == "azure":
        # COMPLETION_MODEL is the *deployment* name here, and COMPLETION_URL the resource root
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(
            azure_endpoint=url, azure_deployment=model, **_openai_kwargs(),
            api_version=os.getenv("AZURE_API_VERSION", "2024-10-21"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"),
        )
    if provider == "vllm":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(base_url=url, model=model, **_openai_kwargs(), api_key=_self_hosted_key())
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(base_url=url, model=model, temperature=COMPLETION_TEMPERATURE)
    raise ValueError(f"Unsupported COMPLETION_MODEL_PROVIDER: {provider}")
