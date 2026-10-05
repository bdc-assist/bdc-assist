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


def test_log_level_and_temperature_from_env(monkeypatch):
    import inspect

    from r_assist import config

    source = inspect.getsource(config.get_llm)
    assert "temperature=0" not in source, "no literal temperature left in get_llm"
    assert source.count("temperature=COMPLETION_TEMPERATURE") == 4, "every provider branch"

    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("COMPLETION_TEMPERATURE", "0.7")
    try:
        importlib.reload(config)
        assert config.LOG_LEVEL == "DEBUG"
        assert config.COMPLETION_TEMPERATURE == 0.7
    finally:
        monkeypatch.undo()
        importlib.reload(config)  # real values (and a fresh get_llm cache) back for later tests
