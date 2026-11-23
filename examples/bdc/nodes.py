"""Workflow nodes of the BDC bot's own. project.yaml puts each in a slot with its settings
(`extra_nodes: {after_agent: {related_work: {label: ..., tool: ...}}}`; slots: before_agent,
after_agent, after_finish, see r_assist/graph.py). Each is a factory, (llm, tools, settings) ->
async node(state) -> state update, or None for no step (e.g. its tool is missing). settings is the
node's mapping in project.yaml: "label" (what the web client shows while it runs), "fields" (state
fields of its own: passed along, returned with the answer), and its own (related_work: "tool").
A before_agent node that returns an answer ends the chat with it. An after_agent node runs beside
output_guardrail and suggest_followups, and returns only the fields it lists (others are logged
and dropped; no two after_agent nodes may list one). A node that raises, or takes longer than its slot's node_timeout
(project.yaml, {slot: seconds}, 30 unless set), is logged and its update dropped.

related_work (after_agent): papers on the question's topic from its "tool" (artie-mcp's
search_publications), asked for or not, into its field "related": returned with the answer beside
its sources, not among them (the answer isn't based on them); a rejected answer drops them. Two LLM calls (related_keywords_human and
related_filter_human in prompts.yaml) decide whether to search at all and which found papers fit. With
USE_DECISION_MODEL, Decision-1 makes both decisions (related_search_decision, related_filter_decision) and
the LLM only writes the search terms.

form_offer (after_agent): a BDC site form for the web client to offer to prefill in its side panel, into its
field "form_offer" ({question, forms: [{id, title, url}]}; question: the contextualized question, which the side
panel drafts from). The forms are bdc-side-panel's (GET /tools at "side_panel_url", SIDE_PANEL_URL overrides
it): one the answer links or cites is offered with no LLM call; otherwise one LLM call (form_offer_human; with
USE_DECISION_MODEL, Decision-1's form_offer_decision) names the one the user explicitly needs, usually none.
bdc-side-panel down: no offer, as its panel couldn't draft one either. A rejected answer drops it.
"""

import asyncio
import logging
import os
import re

import httpx
from langchain_core.output_parsers import MarkdownListOutputParser

from r_assist import config, prompts
from r_assist.graph import _attached_sources

log = logging.getLogger(__name__)

RELATED_TIMEOUT = 8  # seconds related_work waits on its tool: finish (and "done") waits on related_work
RELATED_PAPERS = 3  # related papers kept per sources key: results come ranked by word matches, the tail is noise


def related_work(llm, tools, settings):
    """The tool its settings name ("tool") is called with {"keywords": [...]}; its results' attached sources
    are the related papers. No step when it isn't loaded, or is a stand-in (its server is down)."""
    decide = config.get_decider()
    need = ("related_keywords_human", "related_filter_human") + (
        ("related_search_decision", "related_filter_decision") if decide else ())
    missing = [k for k in need if not prompts.PROMPTS.get(k)]
    if missing:
        raise ValueError(f"related_work: add {', '.join(missing)} to {prompts.DATA_DIR / 'prompts.yaml'}")
    keywords_human, filter_human = prompts.PROMPTS["related_keywords_human"], prompts.PROMPTS["related_filter_human"]
    name = settings.get("tool")
    related = next((t for t in tools if t.name == name), None)
    if not related:
        log.warning("related_work's tool %r is not among the loaded tools (misspelled, or its server is down "
                    "without stand-ins): no related_work step", name)
        return None
    if (related.metadata or {}).get("stand_in"):
        return None  # its server is down: the stand-in only says so, not worth an LLM call a chat

    async def node(state):
        """
        Papers on the question's topic, asked for or not, decided twice. Search: the LLM picks up to 3
        search terms from the question and answer, or "- none" when papers wouldn't help (e.g. a
        question about using the platform itself), and the related tool is called with them. Show:
        the LLM sees the found titles, numbered, and lists the numbers of those that fit, best first,
        or "- none". Up to RELATED_PAPERS of them go in "related" ({sources key: [{title, link,
        type}]}); a paper the answer already cites is a source, so it is left out.
        """
        about = {"question": state["question"], "answer": state["answer"]}
        if decide:
            answers = await decide(about, {"search": {"type": "noul",
                                                      "instructions": prompts.PROMPTS["related_search_decision"]}})
            if answers["search"]["noul"] <= config.DECISION_YES:
                return {}
        resp = await llm.ainvoke([("human", keywords_human.format(input=state["question"], answer=state["answer"]))])
        terms = [t.strip(" \"'.") for t in MarkdownListOutputParser().parse(resp.text)]  # '- "asthma"', '- none.'
        terms = [t for t in terms if t and t.lower() != "none"][:3]  # the prompt asks for 1 to 3
        if not terms:
            return {}
        # a tool call, not plain args: only a ToolMessage carries the artifact the papers ride in
        msg = await asyncio.wait_for(related.ainvoke({"type": "tool_call", "id": "related_work",
                                                      "name": related.name, "args": {"keywords": terms}}),
                                     RELATED_TIMEOUT)
        cited = {s["link"] for items in state.get("sources", {}).values() for s in items}
        found = [(key, s) for key, items in _attached_sources([msg]).items() for s in items if s["link"] not in cited]
        if not found:
            return {}
        # the search ORs every word, so some results share only a word or a program with the question
        if decide:  # a yes/no per paper; the fitting ones best first
            fit = prompts.PROMPTS["related_filter_decision"]
            answers = await decide(about | {"papers": [s["title"] for _, s in found]},
                                   {f"paper_{n}": {"type": "noul", "instructions": fit.format(n=n)}
                                    for n in range(len(found))})
            yes = sorted(((answers[f"paper_{n}"]["noul"], n) for n in range(len(found))
                          if answers[f"paper_{n}"]["noul"] > config.DECISION_YES), reverse=True)
            keep = [found[n] for _, n in yes]
        else:
            papers = "\n".join(f"{n}. {s['title']}" for n, (_, s) in enumerate(found, 1))
            resp = await llm.ainvoke([("human", filter_human.format(input=state["question"], answer=state["answer"],
                                                                    papers=papers))])
            picked = [t.strip(" #.") for t in MarkdownListOutputParser().parse(resp.text)]
            keep = []  # best first, each once; numbers it wasn't given are ignored
            for t in picked:
                if t.isdigit() and 1 <= int(t) <= len(found) and found[int(t) - 1] not in keep:
                    keep.append(found[int(t) - 1])
        related_papers: dict[str, list] = {}
        for key, s in keep[:RELATED_PAPERS]:
            related_papers.setdefault(key, []).append(s)
        return {"related": related_papers} if related_papers else {}

    return node


FORM_OFFER_TIMEOUT = 3  # seconds form_offer waits on bdc-side-panel's form list: finish (and "done") waits on it
_LINK = re.compile(r"https?://[^\s)\]>\"'<]+")


def _same(url):
    return url.rstrip(".,;:").rstrip("/")  # a link at a sentence's end, or without its trailing slash


async def _side_panel_forms(url):
    """bdc-side-panel's forms ({id, title, url, fields}), or [] when it doesn't answer."""
    try:
        async with httpx.AsyncClient(timeout=FORM_OFFER_TIMEOUT) as client:
            resp = await client.get(f"{url.rstrip('/')}/tools")
            resp.raise_for_status()
            return resp.json()["form"]
    except Exception as e:
        log.warning("form_offer: bdc-side-panel at %s listed no forms (%s: %s): no form offered", url,
                    type(e).__name__, e)
        return []


def form_offer(llm, tools, settings):
    """bdc-side-panel's address is its settings' "side_panel_url", or SIDE_PANEL_URL; no step without one."""
    decide = config.get_decider()
    need = ("form_offer_human",) + (("form_offer_decision",) if decide else ())
    missing = [k for k in need if not prompts.PROMPTS.get(k)]
    if missing:
        raise ValueError(f"form_offer: add {', '.join(missing)} to {prompts.DATA_DIR / 'prompts.yaml'}")
    url = os.getenv("SIDE_PANEL_URL") or settings.get("side_panel_url")
    if not url:
        log.warning("form_offer has no side_panel_url in project.yaml, nor SIDE_PANEL_URL: no form_offer step")
        return None
    human = prompts.PROMPTS["form_offer_human"]

    async def node(state):
        """
        The forms the answer links (its sources, or a link in its text), with no LLM call; otherwise the one the
        LLM picks from the numbered titles, or none (Decision-1: a choice of titles or "none").
        """
        forms = await _side_panel_forms(url)
        if not forms:
            return {}
        links = {_same(s["link"]) for items in (state.get("sources") or {}).values() for s in items if s.get("link")}
        links |= {_same(u) for u in _LINK.findall(state.get("answer") or "")}
        offered = [f for f in forms if _same(f["url"]) in links]
        if not offered:
            titles = [f["title"] for f in forms]
            if decide:
                answers = await decide({"question": state["question"], "answer": state["answer"], "forms": titles},
                                       {"form": {"type": "choice", "instructions": prompts.PROMPTS["form_offer_decision"],
                                                 "criteria": {**dict.fromkeys(titles), "none": None}}})
                offered = [f for f in forms if f["title"] == answers["form"]["choice"]][:1]
            else:
                numbered = "\n".join(f"{n}. {t}" for n, t in enumerate(titles, 1))
                resp = await llm.ainvoke([("human", human.format(input=state["question"], answer=state["answer"],
                                                                 forms=numbered))])
                n = int(m.group()) if (m := re.search(r"\d+", resp.text)) else 0  # "2", "- 2"; "none" has none
                offered = [forms[n - 1]] if 1 <= n <= len(forms) else []
        if not offered:
            return {}
        return {"form_offer": {"question": state["question"],
                               "forms": [{k: f[k] for k in ("id", "title", "url")} for f in offered]}}

    return node
