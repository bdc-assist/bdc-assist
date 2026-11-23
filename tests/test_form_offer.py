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
ST = {"id": "stay-connected", "title": "Stay Connected", "url": "https://bdc/stay-connected/", "fields": []}
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
    monkeypatch.setitem(prompts.PROMPTS, "form_offer_pick_human", "Cited forms for {input} / {answer}:\n{forms}")
    monkeypatch.setitem(prompts.PROMPTS, "form_offer_again_human", "{form} again? Offered for:\n{earlier}\nNow: {input} / {answer}")
    forms, asked = [GH, CB, ST], []

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


def test_several_cited_forms_the_llm_picks_one_of_them(offer):
    # a search about one form brings back all of them: one card, not one per cited form
    make, _, _ = offer
    sources = {"bdc-doc": [{"title": t, "link": u, "type": "form"} for t, u in
                           (("Get Help", "https://bdc/help/get-help/"), ("Stay Connected", "https://bdc/stay-connected"))]}
    llm = FakeLLM("2")
    assert [f["id"] for f in run(make(llm), sources=sources)["form_offer"]["forms"]] == ["stay-connected"]
    assert llm.seen[0].startswith("Cited forms") and "1. Get Help\n2. Stay Connected" in llm.seen[0]
    assert "Contact BDC" not in llm.seen[0]  # only the cited ones
    assert run(make(FakeLLM("none")), sources=sources) == {}


def test_several_cited_forms_the_decider_picks_one_of_them(offer, monkeypatch):
    make, _, _ = offer
    monkeypatch.setitem(prompts.PROMPTS, "form_offer_decision", "Which form does the user need?")
    monkeypatch.setitem(prompts.PROMPTS, "form_offer_pick_decision", "Which cited form is it about?")
    asked = []

    async def decide(state, questions):
        asked.append(questions["form"])
        return {"form": {"choice": "Contact BDC"}}

    monkeypatch.setattr(config, "get_decider", lambda: decide)
    out = run(make(FakeLLM()), answer="See https://bdc/help/get-help/ or https://bdc/help/contact-bdc.")
    assert [f["id"] for f in out["form_offer"]["forms"]] == ["contact-bdc"]
    assert asked == [{"type": "choice", "instructions": "Which cited form is it about?",
                      "criteria": {"Get Help": None, "Contact BDC": None, "none": None}}]


# the web client sends back the forms it was offered earlier in the conversation, each with its question
EARLIER = {"client_state": {"offered_forms": [{"id": "get-help", "question": "Who fixes my login loop?"}]}}
CITES_GH = {"bdc-doc": [{"title": "Get Help", "link": "https://bdc/help/get-help/", "type": "form"}]}


def test_a_form_offered_before_is_offered_again_only_for_a_new_request(offer):
    # a long conversation: the same form for another problem gets a new card (a new draft); a question about the
    # one already offered ("what should I put in it?") gets none, its card and draft are already there
    make, _, _ = offer
    new = {"question": "My dbGaP access was approved but I can't see the data. Who can help?", "sources": CITES_GH}
    llm = FakeLLM("new")
    assert run(make(llm), **new, **EARLIER) == {"form_offer": {"question": new["question"], "forms": [OFFER_GH]}}
    assert len(llm.seen) == 1 and "Who fixes my login loop?" in llm.seen[0] and "dbGaP" in llm.seen[0]
    assert "Get Help again?" in llm.seen[0]
    assert run(make(FakeLLM("same")), **new, **EARLIER) == {}
    assert run(make(FakeLLM("")), **new, **EARLIER) == {}  # no clear answer: no second card


def test_a_form_not_offered_before_needs_no_extra_call(offer):
    make, _, _ = offer
    llm = FakeLLM("same")
    cites_cb = {"bdc-doc": [{"title": "Contact BDC", "link": "https://bdc/help/contact-bdc/", "type": "form"}]}
    assert [f["id"] for f in run(make(llm), sources=cites_cb, **EARLIER)["form_offer"]["forms"]] == ["contact-bdc"]
    assert llm.seen == []
    assert run(make(llm), sources=CITES_GH, client_state={})["form_offer"]["forms"] == [OFFER_GH]  # none sent yet
    assert llm.seen == []


def test_the_decider_says_whether_a_form_offered_before_is_needed_again(offer, monkeypatch):
    make, _, _ = offer
    for k in ("form_offer_decision", "form_offer_pick_decision"):
        monkeypatch.setitem(prompts.PROMPTS, k, "?")
    monkeypatch.setitem(prompts.PROMPTS, "form_offer_again_decision", "Needed again, for a new request?")
    for noul, expected in ((0.9, {"form_offer": {"question": STATE["question"], "forms": [OFFER_GH]}}), (0.1, {})):
        asked = []

        async def decide(state, questions, noul=noul):
            asked.append((state, questions))
            return {"again": {"noul": noul}}

        monkeypatch.setattr(config, "get_decider", lambda: decide)
        assert run(make(FakeLLM()), sources=CITES_GH, **EARLIER) == expected
        assert asked[0][1] == {"again": {"type": "noul", "instructions": "Needed again, for a new request?"}}
        assert asked[0][0]["form"] == "Get Help" and asked[0][0]["offered_for"] == ["Who fixes my login loop?"]


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
    monkeypatch.setitem(prompts.PROMPTS, "form_offer_pick_decision", "Which cited form is it about?")
    for choice, expected in (("Get Help", {"form_offer": {"question": STATE["question"], "forms": [OFFER_GH]}}),
                             ("none", {})):
        asked = []

        async def decide(state, questions, choice=choice):
            asked.append((state, questions))
            return {"form": {"choice": choice}}

        monkeypatch.setattr(config, "get_decider", lambda: decide)
        assert run(make(FakeLLM("1"))) == expected
        criteria = asked[0][1]["form"]["criteria"]
        assert set(criteria) == {"Get Help", "Contact BDC", "Stay Connected", "none"}
        assert asked[0][0]["forms"] == ["Get Help", "Contact BDC", "Stay Connected"]


def test_needs_its_prompts(offer, monkeypatch):
    make, _, _ = offer
    monkeypatch.delitem(prompts.PROMPTS, "form_offer_human")
    with pytest.raises(ValueError, match="form_offer_human"):
        make(FakeLLM())
    monkeypatch.setitem(prompts.PROMPTS, "form_offer_human", "{input} {answer} {forms}")
    monkeypatch.delitem(prompts.PROMPTS, "form_offer_pick_human")
    with pytest.raises(ValueError, match="form_offer_pick_human"):
        make(FakeLLM())
    monkeypatch.setitem(prompts.PROMPTS, "form_offer_pick_human", "{input} {answer} {forms}")
    monkeypatch.delitem(prompts.PROMPTS, "form_offer_again_human")
    with pytest.raises(ValueError, match="form_offer_again_human"):
        make(FakeLLM())
    monkeypatch.setitem(prompts.PROMPTS, "form_offer_again_human", "{form} {earlier} {input} {answer}")
    monkeypatch.delitem(prompts.PROMPTS, "form_offer_decision", raising=False)
    monkeypatch.setattr(config, "get_decider", lambda: object())
    with pytest.raises(ValueError, match="form_offer_decision"):
        make(FakeLLM())
