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
related_filter_human in prompts.yaml) decide whether to search at all and which found papers fit.
"""

import asyncio
import logging

from langchain_core.output_parsers import MarkdownListOutputParser

from r_assist import prompts
from r_assist.graph import _attached_sources

log = logging.getLogger(__name__)

RELATED_TIMEOUT = 8  # seconds related_work waits on its tool: finish (and "done") waits on related_work
RELATED_PAPERS = 3  # related papers kept per sources key: results come ranked by word matches, the tail is noise


def related_work(llm, tools, settings):
    """The tool its settings name ("tool") is called with {"keywords": [...]}; its results' attached sources
    are the related papers. No step when it isn't loaded, or is a stand-in (its server is down)."""
    missing = [k for k in ("related_keywords_human", "related_filter_human") if not prompts.PROMPTS.get(k)]
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
