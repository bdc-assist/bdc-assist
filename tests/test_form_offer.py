"""form_offer, an extra node from examples/bdc/nodes.py: offers the BDC site forms an answer links, or the user
explicitly needs, for the web client's side panel to prefill (bdc-side-panel)."""

import asyncio
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from r_assist import config, prompts
from r_assist.graph import load_nodes

GH = {"id": "get-help", "title": "Get Help", "url": "https://bdc/help/get-help/", "fields": [{"name": "x"}]}
CB = {"id": "contact-bdc", "title": "Contact BDC", "url": "https://bdc/help/contact-bdc/", "fields": []}
STATE = {"question": "Who fixes my login loop?", "answer": "Ask the help desk.", "sources": {}}
OFFER_GH = {"id": "get-help", "title": "Get Help", "url": "https://bdc/help/get-help/"}


class FakeLLM:
    def __init__(self, reply=""):
        self.reply, self.seen = reply, []

    async def ainvoke(self, messages):
        self.seen.append(messages[-1][1])
        return AIMessage(content=self.reply)


@pytest.fixture
def offer(monkeypatch):
    """(make(llm, settings?) -> node or None, the side panel's forms list the node will see)."""
    monkeypatch.setattr(config, "get_decider", lambda: None)
    monkeypatch.delenv("SIDE_PANEL_URL", raising=False)
    monkeypatch.setattr(prompts, "DATA_DIR", Path(__file__).resolve().parent.parent / "examples" / "bdc")
    monkeypatch.setitem(prompts.PROMPTS, "form_offer_human", "Forms for {input} / {answer}:\n{forms}")
    forms, asked = [GH, CB], []

    def make(llm, settings=None):
        settings = {"side_panel_url": "http://sp", "fields": ["form_offer"]} if settings is None else settings
        monkeypatch.setitem(prompts.PROJECT, "extra_nodes", {"after_agent": {"form_offer": settings}})
        [(_, factory, loaded)] = load_nodes()["after_agent"]

        async def side_panel_forms(url):
            asked.append(url)
            return list(forms)

        monkeypatch.setitem(factory.__globals__, "_side_panel_forms", side_panel_forms)
        return factory(llm, [], loaded)

    return make, forms, asked


def run(node, **state):
    return asyncio.run(node({**STATE, **state}))


def test_a_cited_form_page_is_offered_without_llm(offer):
    make, _, asked = offer
    llm = FakeLLM("2")
    sources = {"bdc-doc": [{"title": "Get Help", "link": "https://bdc/help/get-help", "type": "page"}]}
    assert run(make(llm), sources=sources) == {"form_offer": {"question": STATE["question"], "forms": [OFFER_GH]}}
    assert llm.seen == [] and asked == ["http://sp"]


def test_a_link_in_the_answer_text_counts(offer):
    make, _, _ = offer
    out = run(make(FakeLLM()), answer="Use [Contact BDC](https://bdc/help/contact-bdc/).")
    assert [f["id"] for f in out["form_offer"]["forms"]] == ["contact-bdc"]


def test_llm_picks_an_explicitly_needed_form(offer):
    make, _, _ = offer
    llm = FakeLLM("2")
    assert [f["id"] for f in run(make(llm))["form_offer"]["forms"]] == ["contact-bdc"]
    assert "1. Get Help" in llm.seen[0] and "2. Contact BDC" in llm.seen[0] and STATE["question"] in llm.seen[0]


def test_llm_none_means_no_offer(offer):
    make, _, _ = offer
    for reply in ("none", "- none", "7", "0", ""):
        assert run(make(FakeLLM(reply))) == {}, reply


def test_side_panel_down_means_no_offer(offer):
    make, forms, _ = offer
    forms.clear()  # _side_panel_forms gives [] when bdc-side-panel doesn't answer
    llm = FakeLLM("1")
    assert run(make(llm)) == {} and llm.seen == []


def test_side_panel_unreachable_gives_no_forms(caplog):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bdc_nodes", Path(__file__).resolve().parent.parent / "examples" / "bdc" / "nodes.py")
    nodes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nodes)
    assert asyncio.run(nodes._side_panel_forms("http://127.0.0.1:9")) == []  # nothing listens on port 9
    assert "bdc-side-panel" in caplog.text


def test_no_url_no_step(offer, caplog):
    make, _, _ = offer
    assert make(FakeLLM(), {"fields": ["form_offer"]}) is None
    assert "side_panel_url" in caplog.text


def test_env_overrides_the_url(offer, monkeypatch):
    make, _, asked = offer
    monkeypatch.setenv("SIDE_PANEL_URL", "http://elsewhere:8012")
    run(make(FakeLLM()))
    assert asked == ["http://elsewhere:8012"]


def test_decider_picks_by_title(offer, monkeypatch):
    make, _, _ = offer
    monkeypatch.setitem(prompts.PROMPTS, "form_offer_decision", "Which form does the user need?")
    for choice, expected in (("Get Help", {"form_offer": {"question": STATE["question"], "forms": [OFFER_GH]}}),
                             ("none", {})):
        asked = []

        async def decide(state, questions, choice=choice):
            asked.append((state, questions))
            return {"form": {"choice": choice}}

        monkeypatch.setattr(config, "get_decider", lambda: decide)
        assert run(make(FakeLLM("1"))) == expected
        criteria = asked[0][1]["form"]["criteria"]
        assert set(criteria) == {"Get Help", "Contact BDC", "none"} and asked[0][0]["forms"] == ["Get Help", "Contact BDC"]


def test_needs_its_prompts(offer, monkeypatch):
    make, _, _ = offer
    monkeypatch.delitem(prompts.PROMPTS, "form_offer_human")
    with pytest.raises(ValueError, match="form_offer_human"):
        make(FakeLLM())
    monkeypatch.setitem(prompts.PROMPTS, "form_offer_human", "{input} {answer} {forms}")
    monkeypatch.delitem(prompts.PROMPTS, "form_offer_decision", raising=False)
    monkeypatch.setattr(config, "get_decider", lambda: object())
    with pytest.raises(ValueError, match="form_offer_decision"):
        make(FakeLLM())
