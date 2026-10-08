"""The r-assist LangGraph workflow.

    START → input_guardrail ──blocked──────────────────────→ END (REFUSAL)
                │ ok
            contextualize → classify ──"r" topic matched───→ END (canned answer)
                                │ regular
                              agent → output_guardrail ──blocked──────────────→ END (REJECT)
                                            │ disclaimers queued └──done──────┐
                                      append_disclaimer ─────→ suggest_followups ─→ END

Both guardrails are hard blocks: the agent's tokens stream as a provisional answer, but a
blocked answer ends the run with the canned reply and blocked=True in the final state.
"""

import json
from typing import TypedDict

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.output_parsers import MarkdownListOutputParser
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from . import prompts
from .prompts import REJECT, REFUSAL  # canned answers live in config/prompts.yaml


class BotState(TypedDict, total=False):
    input: str              # raw user message
    chat_history: list      # (role, content) tuples or BaseMessages
    question: str           # input rewritten as a standalone question
    topics: list[str]       # predefined topics matched by classify
    disclaimers: list[str]  # "a"-topic texts to append after the agent answer
    answer: str             # final response (canned, agent, REFUSAL, or REJECT)
    followups: list[str]    # suggested follow-up questions (empty when none needed)
    sources: dict           # {sources_key: [{title, link, type}]} — distinct documents behind doc_search_tool
    sources_md: str         # the same as a markdown list, ready to show under the answer
    kg: list                # knowledge graphs attached to tool results, one per tool call
    blocked: bool           # an input or output guardrail blocked the run


def _parse_tool_content(content):
    """A ToolMessage holds JSON text, or a list of text blocks each holding JSON
    (the doc server returns one block per chunk). Decode what decodes."""
    blocks = content if isinstance(content, list) else [content]
    parsed = []
    for b in blocks:
        text = b.get("text", "") if isinstance(b, dict) else b
        try:
            parsed.append(json.loads(text))
        except (TypeError, ValueError):
            parsed.append(text)
    return parsed[0] if len(parsed) == 1 else parsed


def _doc_title(m: dict, link: str) -> str:
    """title (front matter, page/video/faq titles) → the document's top heading (first
    entry of the chunk's hierarchy) → last URL segment."""
    if m.get("title"):
        return str(m["title"])
    if m.get("hierarchy"):
        # ponytail: r-doc-builder joins headings with ", ", so a comma inside the top heading truncates it
        return str(m["hierarchy"]).split(", ")[0].strip()
    return link.rstrip("/").rsplit("/", 1)[-1] or link


def _doc_sources(messages) -> list[dict]:
    """Distinct documents behind the doc search tool's chunks, deduplicated on page_url,
    in first-seen order (each call's chunks arrive ranked by relevance)."""
    names = {tc["id"]: tc["name"] for m in messages if isinstance(m, AIMessage) for tc in m.tool_calls or []}
    out, seen = [], set()
    for msg in messages:
        if not (isinstance(msg, ToolMessage) and names.get(msg.tool_call_id, msg.name) == prompts.DOC_SEARCH_TOOL):
            continue
        result = _parse_tool_content(msg.content)
        for c in result if isinstance(result, list) else [result]:
            m = c.get("metadata", {}) if isinstance(c, dict) else {}
            link = m.get("page_url")
            if not link or link in seen:
                continue
            seen.add(link)
            out.append({"title": _doc_title(m, link), "link": link, "type": m.get("doc_type", "")})
    return out


def _kgs(messages) -> list:
    """Knowledge graphs attached to tool results as structured content "kg" (by the server, or
    by a <config dir>/interceptors.py interceptor), in call order. The LLM never sees them."""
    found = [(m.artifact.get("structured_content") or {}).get("kg")
             for m in messages if isinstance(m, ToolMessage) and isinstance(m.artifact, dict)]
    return [kg for kg in found if kg]


def _attached_sources(messages) -> dict[str, list[dict]]:
    """Sources attached to tool results as structured content "sources": {key: [{title, link,
    type}]} (by a <config dir>/interceptors.py interceptor, e.g. the studies Dug cites),
    merged by key, deduplicated on link, in first-seen order. The LLM never sees them."""
    out: dict[str, list[dict]] = {}
    seen = set()
    for m in messages:
        if not (isinstance(m, ToolMessage) and isinstance(m.artifact, dict)):
            continue
        attached = (m.artifact.get("structured_content") or {}).get("sources") or {}
        for key, items in attached.items() if isinstance(attached, dict) else ():
            for item in items if isinstance(items, list) else ():
                link = item.get("link") if isinstance(item, dict) else None
                if link and link not in seen:
                    seen.add(link)
                    out.setdefault(key, []).append({"title": item.get("title") or link, "link": link,
                                                    "type": item.get("type", "")})
    return out


def _sources_md(sources: list[dict]) -> str:
    if not sources:
        return ""
    return prompts.SOURCES.format(items="\n".join(prompts.SOURCES_ITEM.format(**s) for s in sources))


def build_graph(llm, agent, predefined: dict):
    """Compile the workflow.

    llm: any chat model — runs the guardrail/contextualize/classify prompts.
    agent: has astream({'messages': [...]}, stream_mode=["messages", "values"]) —
        produces the real answer, token-streamable.
    predefined: lowercased topic → {response, flag}, from config/predefined_responses.yaml.
        flag "r" = replace: the canned response IS the answer, agent is skipped.
        flag "a" = append: the response is a disclaimer added after the agent answer.
    """
    topic_names = list(predefined)

    async def input_guardrail(state: BotState):
        """
        Yes/no LLM screen of the raw input; "yes" → refuse and end the run.
        """
        try:
            resp = await llm.ainvoke([
                ("system", prompts.INPUT_GUARDRAIL_SYSTEM),
                ("human", prompts.INPUT_GUARDRAIL_HUMAN.format(input=state["input"])),
            ])
            blocked = resp.text.strip().lower().startswith("yes")
        except Exception as e:
            # provider-side content filter (e.g. Azure jailbreak detection) rejects the
            # check request itself — that upstream verdict IS a block
            if "content_filter" not in repr(e):
                raise
            blocked = True
        if blocked:
            return {"blocked": True, "answer": REFUSAL}
        return {"blocked": False}

    async def contextualize(state: BotState):
        """
        Rewrite the input as a standalone question; no-op without history.
        """
        history = state.get("chat_history") or []
        if not history:
            return {"question": state["input"]}
        resp = await llm.ainvoke([
            ("system", prompts.CONTEXTUALIZE_SYSTEM),
            *history,
            ("human", state["input"]),
        ])
        return {"question": resp.text.strip()}

    async def classify(state: BotState):
        """
        Match the question against predefined topics (LLM returns a markdown
        list of names). Any "r" match → canned answer, run ends; "a" matches
        queue their responses as disclaimers and the agent runs normally.
        """
        resp = await llm.ainvoke([
            ("system", prompts.topic_classifier_system(topic_names)),
            ("human", state["question"]),
        ])
        parsed = [t.strip().lower() for t in MarkdownListOutputParser().parse(resp.text)]
        matched = [t for t in parsed if t in predefined]
        update: BotState = {"topics": matched}
        if any(predefined[t]["flag"] == "r" for t in matched):
            update["answer"] = "\n\n".join(predefined[t]["response"] for t in matched)
        else:
            update["disclaimers"] = [
                predefined[t]["response"] for t in matched if predefined[t]["flag"] == "a"
            ]
        return update

    async def run_agent(state: BotState):
        """
        Answer the question with the agent, re-emitting its progress on the
        custom stream: {"type": "status"} when a tool call starts, {"type":
        "token"} per answer token, and {"type": "reset"} when a new model turn
        begins — so only the agent's *last* response survives as the streamed
        provisional answer, which later nodes (guardrail, disclaimers) may
        still replace via the final state. The agent is a nested graph, so the
        outer messages-mode handler never reaches its model — the node must
        stream it itself.
        """
        writer = get_stream_writer()
        result = None
        last_id = None
        async for mode, chunk in agent.astream(
            {"messages": [{"role": "user", "content": state["question"]}]},
            stream_mode=["messages", "values"],
        ):
            if mode == "values":
                result = chunk
                continue
            msg, _meta = chunk
            if not isinstance(msg, AIMessageChunk):
                continue
            for tc in msg.tool_call_chunks or []:
                if tc.get("name"):
                    writer({"type": "status", "text": f"calling {tc['name']}"})
            # .text, not .content: on the Responses API (COMPLETION_REASONING_EFFORT above none) content
            # is a list of reasoning/text/function_call blocks; .text is the text blocks either way
            if msg.text:
                if last_id is not None and msg.id != last_id:
                    writer({"type": "reset"})
                last_id = msg.id
                writer({"type": "token", "text": msg.text})
        docs = _doc_sources(result["messages"])
        attached = _attached_sources(result["messages"])  # e.g. the studies Dug cites
        sources = ({prompts.SOURCES_KEY: docs} if docs else {}) | attached
        sources_md = _sources_md(docs + [s for items in attached.values() for s in items])
        kg = _kgs(result["messages"])
        # sources are known once the agent is done: send them now rather than with "done",
        # which waits for the guardrail and follow-up nodes (a block clears them in "done")
        writer({"type": "sources", "sources": sources, "sources_md": sources_md, "kg": kg})
        return {"answer": result["messages"][-1].text, "sources": sources, "sources_md": sources_md, "kg": kg}

    async def output_guardrail(state: BotState):
        """
        LLM self-check that the answer addresses the question; anything but "yes"
        → replace the streamed answer with REJECT (its sources and kg go with it)
        and end the run.
        """
        # no deterministic name-normalising pass — the agent prompt already enforces the short name
        try:
            resp = await llm.ainvoke([
                ("human", prompts.OUTPUT_GUARDRAIL_HUMAN.format(input=state["question"], answer=state["answer"])),
            ])
            blocked = not resp.text.strip().lower().startswith("yes")
        except Exception as e:
            # provider-side content filter rejects the check request (i.e. the answer) — a block
            if "content_filter" not in repr(e):
                raise
            blocked = True
        if blocked:
            return {"blocked": True, "answer": REJECT, "sources": {}, "sources_md": "", "kg": []}
        return {"blocked": False}

    async def append_disclaimer(state: BotState):
        """
        Tack queued "a"-topic disclaimers onto the final answer.
        """
        return {"answer": "\n\n".join([state["answer"], *state["disclaimers"]])}

    async def suggest_followups(state: BotState):
        """
        Decide if follow-up questions would help; if so, suggest
        prompts.FOLLOWUPS of them (LLM returns a markdown list, or "- none"
        when nothing is needed).
        """
        try:
            resp = await llm.ainvoke([
                ("human", prompts.SUGGEST_FOLLOWUPS_HUMAN.format(
                    input=state["question"], answer=state["answer"])),
            ])
            parsed = [q.strip() for q in MarkdownListOutputParser().parse(resp.text)]
        except Exception:
            # followups are decorative — never lose a good answer over them
            parsed = []
        return {"followups": [q for q in parsed if q.lower() != "none"][:prompts.FOLLOWUPS]}

    def announce(name, fn):
        # emit {"type": "node"} on the custom stream when the node starts, so a
        # streaming client can show progress; no-op writer under plain ainvoke
        async def wrapped(state: BotState):
            get_stream_writer()({"type": "node", "node": name})
            return await fn(state)
        return wrapped

    # Build the graph.
    g = StateGraph(BotState)
    for name, fn in [("input_guardrail", input_guardrail), ("contextualize", contextualize),
                     ("classify", classify), ("agent", run_agent),
                     ("output_guardrail", output_guardrail), ("append_disclaimer", append_disclaimer),
                     ("suggest_followups", suggest_followups)]:
        g.add_node(name, announce(name, fn))

    g.add_edge(START, "input_guardrail")
    # explicit path_maps: runtime doesn't need them, but get_graph()/draw can't see
    # through a bare lambda and would render no edges at all
    g.add_conditional_edges("input_guardrail",
                            lambda s: "blocked" if s["blocked"] else "ok",
                            {"blocked": END, "ok": "contextualize"})
    g.add_edge("contextualize", "classify")
    g.add_conditional_edges("classify",
                            lambda s: "predefined" if s.get("answer") else "regular",
                            {"predefined": END, "regular": "agent"})
    g.add_edge("agent", "output_guardrail")
    # blocked ends the run: no point appending disclaimers to "I couldn't answer"
    g.add_conditional_edges("output_guardrail",
                            lambda s: "blocked" if s["blocked"]
                            else "disclaimer" if s.get("disclaimers") else "done",
                            {"blocked": END, "disclaimer": "append_disclaimer",
                             "done": "suggest_followups"})
    g.add_edge("append_disclaimer", "suggest_followups")
    g.add_edge("suggest_followups", END)
    return g.compile()
