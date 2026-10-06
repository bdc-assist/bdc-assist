"""The bdc-assist LangGraph workflow.

    START → input_guardrail ──blocked──────────────────────→ END (REFUSAL)
                │ ok
            contextualize → classify ──"r" topic matched───→ END (canned answer)
                                │ regular
                              agent → output_guardrail ──rejected──→ output_reject ─→ END (REJECT)
                                            │ disclaimers queued └──done──────┐
                                      append_disclaimer ─────→ suggest_followups ─→ END
"""

import json
from typing import TypedDict

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.output_parsers import MarkdownListOutputParser
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from . import prompts
from .prompts import REJECT, REFUSAL  # canned answers live in data/prompts.yaml


class BotState(TypedDict, total=False):
    input: str              # raw user message
    chat_history: list      # (role, content) tuples or BaseMessages
    question: str           # input rewritten as a standalone question
    topics: list[str]       # predefined topics matched by classify
    disclaimers: list[str]  # "a"-topic texts to append after the agent answer
    answer: str             # final response (canned, agent, REFUSAL, or REJECT)
    followups: list[str]    # suggested follow-up questions (empty when none needed)
    tool_results: list      # every tool call the agent made: {tool, args, result} — doc chunks
                            # with their metadata, knowledge-graph rows, etc., for the client to show
    sources: dict           # {"bdc-doc": [...], "dug": [...]} of {title, link, type}: docs behind search_docs, studies from Dug
    sources_md: str         # the same as a markdown list, ready to append to the answer
    graph: dict             # {"nodes": [...], "edges": [...]}: concepts, variables, studies from Dug
    blocked: bool           # input_guardrail verdict
    rejected: bool          # output_guardrail rejected the agent answer


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


def _tool_results(messages) -> list[dict]:
    """Pair each ToolMessage with the AI tool call that produced it."""
    calls = {tc["id"]: tc for m in messages if isinstance(m, AIMessage) for tc in (m.tool_calls or [])}
    return [{"tool": calls.get(m.tool_call_id, {}).get("name", m.name),
             "args": calls.get(m.tool_call_id, {}).get("args", {}),
             "result": _parse_tool_content(m.content)}
            for m in messages if isinstance(m, ToolMessage)]


def _doc_title(m: dict, link: str) -> str:
    """title (faq, video, news, fellows) → last breadcrumb (gitbook docs) → first
    heading (website pages) → last URL segment."""
    if m.get("title"):
        return str(m["title"])
    h = m.get("hierarchy")
    if isinstance(h, list) and h:
        return str(h[-1])
    if isinstance(h, str) and h:
        return h.split(">")[-1].strip()
    if m.get("headings"):
        return str(m["headings"]).split(",")[0].strip()
    return link.rstrip("/").rsplit("/", 1)[-1] or link


def _doc_sources(tool_results: list) -> list[dict]:
    """Distinct documents behind the search_docs chunks, deduplicated on page_url,
    in first-seen order (each call's chunks arrive ranked by relevance)."""
    out, seen = [], set()
    for tr in tool_results:
        if tr["tool"] != "search_docs":
            continue
        chunks = tr["result"] if isinstance(tr["result"], list) else [tr["result"]]
        for c in chunks:
            m = c.get("metadata", {}) if isinstance(c, dict) else {}
            link = m.get("page_url")
            if not link or link in seen:
                continue
            seen.add(link)
            out.append({"title": _doc_title(m, link), "link": link, "type": m.get("doc_type", "")})
    return out


def _knowledge_graph(tool_results: list) -> dict:
    """The graph behind Dug's get_concept_graph calls. Each result row
    {concept_id, concept, concept_type, variable_id, variable_name, study_id,
    study_name, related_concepts_count} links one variable to the concept and to its
    study. Returns {"nodes": [{id, label, type}], "edges": [{source, target}]}, type
    being "concept" | "variable" | "study"; concepts also carry Dug's concept_type,
    variables its related_concepts_count. Edges run variable → concept and variable →
    study, untyped (Dug names no relation). Nodes and edges are deduplicated across
    calls; empty dict when there were no such calls."""
    nodes, edges = {}, {}
    for tr in tool_results:
        rows = tr["result"].get("graph") if tr["tool"] == "get_concept_graph" and isinstance(tr["result"], dict) else None
        for r in rows if isinstance(rows, list) else []:
            cid, vid, sid = r["concept_id"], r["variable_id"], r["study_id"]
            nodes.setdefault(cid, {"id": cid, "label": r.get("concept", cid), "type": "concept",
                                   "concept_type": r.get("concept_type", "")})
            nodes.setdefault(vid, {"id": vid, "label": r.get("variable_name", vid), "type": "variable",
                                   "related_concepts_count": r.get("related_concepts_count", 0)})
            nodes.setdefault(sid, {"id": sid, "label": r.get("study_name", sid), "type": "study"})
            for target in (cid, sid):
                edges.setdefault((vid, target), {"source": vid, "target": target})
    return {"nodes": list(nodes.values()), "edges": list(edges.values())} if nodes else {}


def _dug_sources(tool_results: list) -> list[dict]:
    """The studies Dug cites: every result's "_sources" list ({title, link, type:
    "dbgap-study"}, one per study), deduplicated on link in first-seen order — two
    concept graphs often share studies."""
    out, seen = [], set()
    for tr in tool_results:
        r = tr["result"]
        for s in r.get("_sources", []) if isinstance(r, dict) else []:
            if isinstance(s, dict) and s.get("link") and s["link"] not in seen:
                seen.add(s["link"])
                out.append({"title": s.get("title") or s["link"], "link": s["link"], "type": s.get("type", "")})
    return out


def _sources_md(sources: list[dict]) -> str:
    if not sources:
        return ""
    return "**Sources**\n" + "\n".join(f"- [{s['title']}]({s['link']}) ({s['type']})" for s in sources)


def build_graph(llm, agent, predefined: dict):
    """Compile the workflow.

    llm: any chat model — runs the guardrail/contextualize/classify prompts.
    agent: has astream({'messages': [...]}, stream_mode=["messages", "values"]) —
        produces the real answer, token-streamable.
    predefined: lowercased topic → {response, flag}, from data/prompts.yaml.
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
            blocked = resp.content.strip().lower().startswith("yes")
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
        return {"question": resp.content.strip()}

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
        parsed = [t.strip().lower() for t in MarkdownListOutputParser().parse(resp.content)]
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
            if isinstance(msg.content, str) and msg.content:
                if last_id is not None and msg.id != last_id:
                    writer({"type": "reset"})
                last_id = msg.id
                writer({"type": "token", "text": msg.content})
        tool_results = _tool_results(result["messages"])
        docs, dug = _doc_sources(tool_results), _dug_sources(tool_results)
        sources = {k: v for k, v in (("bdc-doc", docs), ("dug", dug)) if v}
        sources_md = _sources_md(docs + dug)
        # sources are final once the agent is done: send them now rather than with
        # "done", which waits for the guardrail and follow-up nodes
        writer({"type": "sources", "sources": sources, "sources_md": sources_md})
        kg = _knowledge_graph(tool_results)
        if kg:
            writer({"type": "graph", "graph": kg})
        return {"answer": result["messages"][-1].content, "tool_results": tool_results,
                "sources": sources, "sources_md": sources_md, "graph": kg}

    async def output_guardrail(state: BotState):
        """
        LLM self-check that the answer addresses the question; "no" → REJECT.
        """
        # no normalize_bdc_names pass — the agent prompt already enforces "BDC"
        answer = state["answer"]
        resp = await llm.ainvoke([
            ("human", prompts.OUTPUT_GUARDRAIL_HUMAN.format(input=state["question"], answer=answer)),
        ])
        if not resp.content.strip().lower().startswith("yes"):
            return {"rejected": True}
        return {"answer": answer}

    async def output_reject(state: BotState):
        """
        Replace a rejected answer with the canned REJECT reply, and drop its sources and
        graph — they belong to the answer that was rejected. tool_results stay, for debugging.
        """
        return {"answer": REJECT, "sources": {}, "sources_md": "", "graph": {}}

    async def append_disclaimer(state: BotState):
        """
        Tack queued "a"-topic disclaimers onto the final answer.
        """
        return {"answer": "\n\n".join([state["answer"], *state["disclaimers"]])}

    async def suggest_followups(state: BotState):
        """
        Decide if follow-up questions would help; if so, suggest 3
        (LLM returns a markdown list, or "- none" when nothing is needed).
        """
        try:
            resp = await llm.ainvoke([
                ("human", prompts.SUGGEST_FOLLOWUPS_HUMAN.format(
                    input=state["question"], answer=state["answer"])),
            ])
            parsed = [q.strip() for q in MarkdownListOutputParser().parse(resp.content)]
        except Exception:
            # followups are decorative — never lose a good answer over them
            parsed = []
        return {"followups": [q for q in parsed if q.lower() != "none"][:3]}

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
                     ("output_guardrail", output_guardrail), ("output_reject", output_reject),
                     ("append_disclaimer", append_disclaimer),
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
    # rejected goes to output_reject then END: no point appending disclaimers to "I couldn't answer"
    g.add_conditional_edges("output_guardrail",
                            lambda s: "rejected" if s.get("rejected")
                            else "disclaimer" if s.get("disclaimers") else "done",
                            {"rejected": "output_reject", "disclaimer": "append_disclaimer",
                             "done": "suggest_followups"})
    g.add_edge("output_reject", END)
    g.add_edge("append_disclaimer", "suggest_followups")
    g.add_edge("suggest_followups", END)
    return g.compile()
