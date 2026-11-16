import importlib


def test_cors_origins_from_env(monkeypatch):
    from r_assist import config

    monkeypatch.setenv("CORS_ORIGINS", "http://a.example, http://b.example,")
    try:
        importlib.reload(config)
        assert config.CORS_ORIGINS == ["http://a.example", "http://b.example"]
    finally:
        monkeypatch.undo()
        importlib.reload(config)  # put the real CORS_ORIGINS (and a fresh get_llm cache) back for later tests


def test_provider_selection_and_temperature_reach_every_client(monkeypatch):
    """Which client the COMPLETION_* settings build, where it points, and that COMPLETION_TEMPERATURE
    reaches each one: a branch that dropped it would silently sample at the provider's default."""
    from r_assist import config

    for var in ("COMPLETION_MODEL_PROVIDER", "COMPLETION_URL", "COMPLETION_REASONING_EFFORT"):
        monkeypatch.setenv(var, "")  # empty, not deleted: reload's load_dotenv would refill it from .env
    monkeypatch.setenv("COMPLETION_TEMPERATURE", "0.5")
    monkeypatch.setenv("COMPLETION_MODEL", "m")
    monkeypatch.setenv("OPENAI_API_KEY", "test")  # the clients are only built, never called
    cases = [  # env -> (client class, the endpoint attribute that must carry COMPLETION_URL)
        ({}, ("ChatOpenAI", None)),
        ({"COMPLETION_URL": "https://res.openai.azure.com"}, ("AzureChatOpenAI", "azure_endpoint")),
        ({"COMPLETION_URL": "http://gpu:8000/v1"}, ("ChatOpenAI", "openai_api_base")),  # vllm
        ({"COMPLETION_URL": "http://ollama:11434"}, ("ChatOllama", "base_url")),
        ({"COMPLETION_URL": "http://gpu:8000/v1", "COMPLETION_MODEL_PROVIDER": "Ollama  # local"},
         ("ChatOllama", "base_url")),  # explicit provider wins; a trailing .env comment is dropped
    ]
    try:
        importlib.reload(config)
        for env, (cls, endpoint) in cases:
            with monkeypatch.context() as m:
                for k, v in env.items():
                    m.setenv(k, v)
                config.get_llm.cache_clear()
                llm = config.get_llm()
                assert type(llm).__name__ == cls, (env, type(llm).__name__)
                assert llm.temperature == 0.5, env
                assert endpoint is None or getattr(llm, endpoint) == env["COMPLETION_URL"], env
        monkeypatch.setenv("COMPLETION_MODEL_PROVIDER", "bedrock")
        config.get_llm.cache_clear()
        try:
            config.get_llm()
            raise AssertionError("an unknown provider must fail")
        except ValueError as e:
            assert "bedrock" in str(e)
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_log_level_and_temperature_from_env(monkeypatch):
    from r_assist import config

    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("COMPLETION_TEMPERATURE", "0.7")
    try:
        importlib.reload(config)
        assert config.LOG_LEVEL == "DEBUG"
        assert config.COMPLETION_TEMPERATURE == 0.7
    finally:
        monkeypatch.undo()
        importlib.reload(config)  # real values (and a fresh get_llm cache) back for later tests


def test_reasoning_effort_settings(monkeypatch):
    from r_assist import config

    monkeypatch.setenv("COMPLETION_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test")  # get_llm only builds the client
    monkeypatch.setenv("COMPLETION_TEMPERATURE", "0")
    # effort -> (reasoning_effort sent, temperature sent, Responses API), all tested against the live API:
    # unset: gpt-4o-mini rejects any reasoning_effort, even "none"
    # none: gpt-6-luna with reasoning off takes temperature 0 and calls tools on chat completions
    # medium: gpt-6-luna reasoning takes only its default temperature, and calls tools only on Responses
    cases = [("", None, 0.0, False), ("none", "none", 0.0, False), ("medium", "medium", None, True)]
    try:
        for value, effort, temperature, responses in cases:
            monkeypatch.setenv("COMPLETION_REASONING_EFFORT", value)
            importlib.reload(config)
            llm = config.get_llm()
            params = llm._default_params
            assert (params.get("reasoning_effort"), params.get("temperature"), bool(llm.use_responses_api)) \
                == (effort, temperature, responses), value
            assert ("reasoning_effort" in params) == (effort is not None), "unset is not sent at all"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_decision_model_settings(monkeypatch):
    """USE_DECISION_MODEL: off unless "true"; on, Decision-1's System One endpoint on the host of
    DECISION_URL, else of COMPLETION_URL / OPENAI_BASE_URL, with the deployment DECISION_MODEL."""
    import asyncio

    import httpx
    import openai

    from r_assist import config

    seen = {}

    class FakeClient:  # stands in for AsyncOpenAI: the request it would send, no network
        def __init__(self, **kwargs):
            seen["client"] = kwargs

        async def post(self, path, body, cast_to):
            seen["post"] = (path, body)
            return httpx.Response(200, json={"answers": {"x": {"type": "noul", "noul": 0.9}}})

    for var in ("USE_DECISION_MODEL", "DECISION_URL", "DECISION_MODEL", "COMPLETION_URL", "OPENAI_BASE_URL"):
        monkeypatch.setenv(var, "")  # empty, not deleted: reload's load_dotenv would refill it from .env
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)
    try:
        importlib.reload(config)
        assert config.get_decider() is None, "off by default: the chat model decides, as before"
        monkeypatch.setenv("USE_DECISION_MODEL", "True  # on")
        importlib.reload(config)
        try:
            config.get_decider()
            raise AssertionError("no URL to find the Azure resource by must fail")
        except ValueError as e:
            assert "DECISION_URL" in str(e)
        for env, host in [({"OPENAI_BASE_URL": "https://res.services.ai.azure.com/openai/v1"}, "https://res.services.ai.azure.com"),
                          ({"COMPLETION_URL": "https://a.services.ai.azure.com/openai/v1",
                            "DECISION_URL": "https://b.services.ai.azure.com"}, "https://b.services.ai.azure.com")]:
            with monkeypatch.context() as m:
                for k, v in env.items():
                    m.setenv(k, v)
                config.get_decider.cache_clear()
                answers = asyncio.run(config.get_decider()("state", {"x": {"type": "noul", "instructions": "?"}}))
            assert seen["client"] == {"base_url": f"{host}/providers/microsoft/v1", "api_key": "key"}, env
            assert seen["post"] == ("/systemone", {"model": "Decision-1", "state": "state",
                                                   "questions": {"x": {"type": "noul", "instructions": "?"}}})
            assert answers == {"x": {"type": "noul", "noul": 0.9}}
    finally:
        monkeypatch.undo()
        importlib.reload(config)
