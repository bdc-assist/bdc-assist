"""The r-assist LangGraph workflow.

    START → contextualize ─┬→ input_guardrail ─┐
                           └→ classify ─────────┴→ gate ─┬─ blocked ────────────→ END (REFUSAL)
                                                         ├─ "r" topic matched ──→ END (canned answer)
                                                         └─ regular ────────────→ main_agent

    main_agent ─┬→ output_guardrail ──┐
                ├→ suggest_followups ─┼→ finish ─┬─ disclaimer ──────→ append_disclaimer → END (answer + disclaimers)
                └→ related_work ──────┘          └─ done or blocked ─→ END (answer, or REJECT)

input_guardrail and classify run side by side: one LLM round trip before the agent instead of two
(~1.6 s a chat, measured). gate joins them, and a blocked input wins over anything classify matched.
contextualize goes first so classify gets the standalone question; it calls the LLM only when there
is chat history. output_guardrail and suggest_followups run side by side too; finish joins them, and
a rejected answer drops the follow-ups and related papers (wasted calls, on a rare path). An answer
that passed gets its "a"-topic disclaimers in append_disclaimer, after the join.

related_work runs beside them only when project.yaml names a related_tool that is among the agent's
tools and not a stand-in (BDC: artie-mcp's search_publications): papers on the question's topic,
asked for or not.
finish merges them into the sources; like the follow-ups they are extras, and a failure drops them.

Both guardrails are hard blocks: the agent's tokens stream as a provisional answer, but a
blocked answer ends the run with the canned reply and blocked="input" or "output" (which
guardrail) in the final state.
"""

import asyncio
import json
import logging
from typing import TypedDict

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.output_parsers import MarkdownListOutputParser
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from . import prompts
from .prompts import REJECT, REFUSAL  # canned answers live in config/prompts.yaml

log = logging.getLogger(__name__)

RELATED_TIMEOUT = 8  # seconds related_work waits on its tool: finish (and "done") waits on related_work
RELATED_PAPERS = 3  # related papers kept per sources key: results come ranked by word matches, the tail is noise


class BotState(TypedDict, total=False):
    input: str              # raw user message
    chat_history: list      # (role, content) tuples or BaseMessages
    question: str           # input rewritten as a standalone question
    topics: list[str]       # predefined topics matched by classify
    disclaimers: list[str]  # "a"-topic texts to append after the agent answer
    answer: str             # final response (canned, agent, REFUSAL, or REJECT)
    followups: list[str]    # suggested follow-up questions (empty when none needed)
    sources: dict           # {sources_key: [{title, link, type}]} — distinct documents behind doc_search_tool,
                            # plus {"predefined": [...]} for the predefined responses shown (link may be "")
    sources_md: str         # the same as a markdown list, ready to show under the answer
    kg: list                # knowledge graphs attached to tool results, one per tool call
    related: dict           # {key: [{title, link, type}]} — related_work's papers; finish merges them into sources
    blocked: str | None     # which guardrail blocked the run: "input" (question refused) or
                            # "output" (answer rejected); None when neither did


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
    return prompts.SOURCES.format(items="\n".join(
        (prompts.SOURCES_ITEM if s["link"] else prompts.SOURCES_ITEM_NO_LINK).format(**s) for s in sources))


def build_graph(llm, agent, predefined: dict, tools=()):
    """Compile the workflow.

    llm: any chat model — runs the guardrail/contextualize/classify prompts.
    agent: has astream({'messages': [...]}, stream_mode=["messages", "values"]) —
        produces the real answer, token-streamable.
    predefined: lowercased topic → {response, flag, link?, title?}, from config/predefined_responses.yaml.
        flag "r" = replace: the canned response IS the answer, agent is skipped.
        flag "a" = append: the response is a disclaimer added after the agent answer.
    tools: the agent's tools; the one named prompts.RELATED_TOOL, if any, is related_work's. It is
        called with {"keywords": [...]} and its results' attached sources are the related papers.
    """
    topic_names = list(predefined)
    related = next((t for t in tools if t.name == prompts.RELATED_TOOL), None)
    if prompts.RELATED_TOOL and not related:
        log.warning("related_tool %r is not among the loaded tools (misspelled, or its server is down "
                    "without stand-ins): no related_work step", prompts.RELATED_TOOL)
    if related and (related.metadata or {}).get("stand_in"):
        related = None  # its server is down: the stand-in only says so, not worth an LLM call a chat

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
        # just the verdict: classify may set a canned answer in this same step, so gate writes the refusal
        return {"blocked": "input" if blocked else None}

    async def contextualize(state: BotState):
        """
        Rewrite the input as a standalone question; no-op without history.
        """
        history = state.get("chat_history") or []
        if not history:
            return {"question": state["input"]}
        try:
            resp = await llm.ainvoke([
                ("system", prompts.CONTEXTUALIZE_SYSTEM),
                *history,
                ("human", state["input"]),
            ])
        except Exception as e:
            # runs before the input guardrail: a provider content filter rejecting the conversation is
            # the guardrail's block to report (its own check is filtered too), not a crash
            if "content_filter" not in repr(e):
                raise
            return {"question": state["input"]}
        return {"question": resp.text.strip()}

    async def classify(state: BotState):
        """
        Match the question against predefined topics (LLM returns a markdown
        list of names). Any "r" match → canned answer, run ends; "a" matches
        queue their responses as disclaimers and the agent runs normally.
        """
        try:
            resp = await llm.ainvoke([
                ("system", prompts.topic_classifier_system(topic_names)),
                ("human", state["question"]),
            ])
            text = resp.text
        except Exception as e:
            # alongside the input guardrail: a filtered question is its block to report, not a crash
            if "content_filter" not in repr(e):
                raise
            text = ""
        parsed = [t.strip().lower() for t in MarkdownListOutputParser().parse(text)]
        matched = [t for t in parsed if t in predefined]
        update: BotState = {"topics": matched}
        if any(predefined[t]["flag"] == "r" for t in matched):
            used = matched
            update["answer"] = "\n\n".join(predefined[t]["response"] for t in used)
        else:
            used = [t for t in matched if predefined[t]["flag"] == "a"]
            update["disclaimers"] = [predefined[t]["response"] for t in used]
        # the responses shown are sources too, even without a link; run_agent adds the agent's
        pre = [{"title": predefined[t].get("title", t), "link": predefined[t].get("link", ""), "type": "predefined"}
               for t in used]
        update["sources"] = {"predefined": pre} if pre else {}
        update["sources_md"] = _sources_md(pre)
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
        # e.g. the studies Dug cites, then classify's "a" topics
        extra = _attached_sources(result["messages"]) | state.get("sources", {})
        sources = ({prompts.SOURCES_KEY: docs} if docs else {}) | extra
        sources_md = _sources_md(docs + [s for items in extra.values() for s in items])
        kg = _kgs(result["messages"])
        # the agent's sources are known now: send them rather than wait for "done", which waits for
        # the guardrail, follow-up and related_work nodes (a block clears them in "done", where
        # related_work's papers join them)
        writer({"type": "sources", "sources": sources, "sources_md": sources_md, "kg": kg})
        return {"answer": result["messages"][-1].text, "sources": sources, "sources_md": sources_md, "kg": kg}

    async def output_guardrail(state: BotState):
        """
        LLM self-check that the answer addresses the question; anything but "yes"
        → replace the streamed answer with REJECT (its sources and kg go with it;
        finish drops the follow-ups and related papers, and no disclaimers follow).
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
            return {"blocked": "output", "answer": REJECT, "sources": {}, "sources_md": "", "kg": []}
        return {"blocked": None}

    async def suggest_followups(state: BotState):
        """
        Decide if follow-up questions would help; if so, suggest
        prompts.FOLLOWUPS of them (LLM returns a markdown list, or "- none"
        when nothing is needed). Sees the agent's answer, without disclaimers.
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

    async def related_work(state: BotState):
        """
        Papers on the question's topic, asked for or not: the LLM picks up to 4 search terms from the
        question and answer (a markdown list, or "- none" when it isn't about research), and the
        related tool is called with them. Its attached sources wait in "related" for finish.
        """
        try:
            resp = await llm.ainvoke([
                ("human", prompts.RELATED_KEYWORDS_HUMAN.format(input=state["question"], answer=state["answer"])),
            ])
            terms = [t.strip(" \"'.") for t in MarkdownListOutputParser().parse(resp.text)]  # '- "asthma"', '- none.'
            terms = [t for t in terms if t and t.lower() != "none"][:3]  # the prompt asks for 1 to 3
            if not terms:
                return {}
            # a tool call, not plain args: only a ToolMessage carries the artifact the papers ride in
            msg = await asyncio.wait_for(related.ainvoke({"type": "tool_call", "id": "related_work",
                                                          "name": related.name, "args": {"keywords": terms}}),
                                         RELATED_TIMEOUT)
        except Exception as e:
            # related papers are extras, like follow-ups — never lose a good answer over them; but say
            # why, or a bad related_tool argument or prompt placeholder turns the step off unseen
            log.warning("related_work skipped: %s: %s", type(e).__name__, e)
            return {}
        return {"related": {key: items[:RELATED_PAPERS] for key, items in _attached_sources([msg]).items()}}

    async def gate(state: BotState):
        """
        Join of input_guardrail and classify, which run side by side: a blocked input gets the
        refusal and nothing classify found (canned answer, disclaimers, their sources).
        """
        if state.get("blocked"):
            return {"answer": REFUSAL, "topics": [], "disclaimers": [], "sources": {}, "sources_md": ""}
        return {}

    async def finish(state: BotState):
        """
        Join of output_guardrail, suggest_followups and related_work, which run side by side: a
        rejected answer gets no follow-ups (nor related papers: its sources are gone); otherwise
        related papers join the sources, each link once, and an answer with "a"-topic
        disclaimers goes on to append_disclaimer.
        """
        if state["blocked"]:
            return {"followups": []}
        update: BotState = {}
        if state.get("related"):
            sources = {key: list(items) for key, items in state["sources"].items()}
            seen = {s["link"] for items in sources.values() for s in items}
            for key, items in state["related"].items():
                for s in items:
                    if s["link"] not in seen:
                        seen.add(s["link"])
                        sources.setdefault(key, []).append(s)
            update["sources"] = sources
            update["sources_md"] = _sources_md([s for items in sources.values() for s in items])
        return update

    async def append_disclaimer(state: BotState):
        """
        Add the queued "a"-topic disclaimers to an answer that passed the output check.
        """
        return {"answer": "\n\n".join([state["answer"], *state["disclaimers"]])}

    def announce(name, fn):
        # emit {"type": "node"} on the custom stream when the node starts, so a
        # streaming client can show progress; no-op writer under plain ainvoke
        async def wrapped(state: BotState):
            get_stream_writer()({"type": "node", "node": name})
            return await fn(state)
        return wrapped

    # Build the graph.
    g = StateGraph(BotState)
    after_agent = [("output_guardrail", output_guardrail), ("suggest_followups", suggest_followups)]
    if related:
        after_agent.append(("related_work", related_work))
    for name, fn in [("input_guardrail", input_guardrail), ("contextualize", contextualize),
                     ("classify", classify), ("main_agent", run_agent), *after_agent,
                     ("append_disclaimer", append_disclaimer)]:
        g.add_node(name, announce(name, fn))
    g.add_node("gate", gate)  # not announced: instant, no step a user would wait on
    g.add_node("finish", finish)  # same

    g.add_edge(START, "contextualize")
    g.add_edge("contextualize", "input_guardrail")
    g.add_edge("contextualize", "classify")
    g.add_edge(["input_guardrail", "classify"], "gate")  # waits for both
    # explicit path_maps: runtime doesn't need them, but get_graph()/draw can't see
    # through a bare lambda and would render no edges at all. After gate an answer exists only if
    # the input was refused or matched an "r" topic; one key, as a drawing shows one edge per target
    g.add_conditional_edges("gate",
                            lambda s: "blocked or canned" if s.get("answer") else "regular",
                            {"blocked or canned": END, "regular": "main_agent"})
    for name, _ in after_agent:
        g.add_edge("main_agent", name)
    g.add_edge([name for name, _ in after_agent], "finish")  # waits for all of them
    # one key per target, as at gate: a drawing shows one edge to END, so a separate "rejected" key would vanish
    g.add_conditional_edges("finish",
                            lambda s: "disclaimer" if not s["blocked"] and s.get("disclaimers") else "done or blocked",
                            {"disclaimer": "append_disclaimer", "done or blocked": END})
    g.add_edge("append_disclaimer", END)
    return g.compile()


def drawing(graph, agent, servers: dict) -> str:
    """For docs/workflow_detailed.png, as mermaid text: graph.get_graph() with the main_agent node
    drawn as a box holding the agent's own model ⇄ tools loop (agent.get_graph()) and the MCP servers
    its tools call (mcp_servers.yaml entries, each labelled with its interceptors). The agent runs
    inside main_agent's function, so get_graph(xray=True) can't see it. Edges go to and from the box itself, so it
    sits in the main flow like a node; deepagents' middleware hooks ("X.before_agent") and the
    model's self-loop are internals, left out."""
    from langchain_core.runnables.graph import Edge, Node
    from langchain_core.runnables.graph_mermaid import draw_mermaid

    outer, inner = graph.get_graph(), agent.get_graph()
    nodes = {k: n for k, n in outer.nodes.items() if k != "main_agent"}  # main_agent becomes the box
    keep = {k for k in inner.nodes if k not in (START, END) and "." not in k}  # model, tools
    nodes |= {f"main_agent:{k}": inner.nodes[k]._replace(id=f"main_agent:{k}", name=k) for k in keep}
    edges = list(outer.edges)
    edges += [e._replace(source=f"main_agent:{e.source}", target=f"main_agent:{e.target}") for e in inner.edges
              if {e.source, e.target} <= keep and e.source != e.target]
    for name, conn in servers.items():
        label = " + ".join([name, *(conn.get("interceptors") or [])])
        nodes[f"main_agent:{name}"] = Node(id=f"main_agent:{name}", name=label, data=None, metadata=None)
        edges.append(Edge("main_agent:tools", f"main_agent:{name}", conditional=True))
    # shortcut: draw_mermaid, not Graph.draw_mermaid, which rejects edges to a box; if a langchain
    # upgrade breaks it, test_drawing_shows_the_agent_inside fails
    return draw_mermaid(nodes, edges, first_node=START, last_node=END)
