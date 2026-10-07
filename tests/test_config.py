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
