from tests.make_kg_fixtures import KEYWORDS, OUT, text


def test_kg_fixtures_are_up_to_date():
    """tests/fixtures/kg/ is what the server sends for each stub keyword; the stub serves it and
    bdc-assist-client tests against it. Out of date: run uv run python tests/make_kg_fixtures.py"""
    assert sorted(p.stem for p in OUT.glob("*.json")) == sorted(KEYWORDS)
    for keyword in KEYWORDS:
        assert (OUT / f"{keyword}.json").read_text(encoding="utf-8") == text(keyword), keyword
