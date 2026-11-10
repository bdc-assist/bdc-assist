"""The r-assist LangGraph workflow.

    START → contextualize ─┬→ input_guardrail ─┐
                           └→ classify ─────────┴→ gate ─┬─ blocked ────────────→ END (REFUSAL)
                                                         ├─ "r" topic matched ──→ END (canned answer)
                                                         └─ regular ────────────→ [before_agent] ─┬─ go on ───→ main_agent
                                                                                                  └─ answered → END (its answer)

    main_agent ─┬→ output_guardrail ───┐
                ├→ (suggest_followups) ┼→ finish ─┬─ blocked ─→ END (REJECT)
                └→ [after_agent] ──────┘          └─ passed ──→ [after_finish] ─┬─ disclaimer → (append_disclaimer) → END
                                                                                └─ done ──────→ END

[slot]: the config's own nodes (project.yaml's extra_nodes, see load_nodes); (node): optional, on by
default (followups: 0 and append_disclaimer: false in project.yaml turn them off). A drawing shows
only what the config has.

input_guardrail and classify run side by side: one LLM round trip before the agent instead of two
(~1.6 s a chat, measured). gate joins them, and a blocked input wins over anything classify matched.
contextualize goes first so classify gets the standalone question; it calls the LLM only when there
is chat history. output_guardrail and suggest_followups run side by side too; finish joins them, and
a rejected answer drops the follow-ups and the after_agent nodes' fields (wasted calls, on a rare
path). An answer that passed gets
its "a"-topic disclaimers in append_disclaimer, after the join.

The config's nodes are functions in <config dir>/nodes.py. before_agent ones run in order on a
regular question (e.g. rewriting it for the agent); one that returns an answer ends the run there,
like a canned answer (e.g. a check of the config's own, or a cached reply). after_agent ones run
beside output_guardrail and each returns only the fields it lists (two steps side by side writing one
field would fail the chat, so no two after_agent nodes may list one, nor one a BotState field);
after_finish ones run in order on an answer that passed (e.g.
reshaping it, or adding to its sources what an after_agent node found), before its disclaimers.
Like the follow-ups they are extras: one that fails is logged and skipped. A field of their own,
beyond BotState's, must be in a node's "fields" in project.yaml (then the API returns it too); one
that isn't is logged and dropped.

Both guardrails are hard blocks: the agent's tokens stream as a provisional answer, but a
blocked answer ends the run with the canned reply and blocked="input" or "output" (which
guardrail) in the final state.
"""

import asyncio
import importlib.util
import json
import logging
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.output_parsers import MarkdownListOutputParser
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from . import prompts
from .prompts import REJECT, REFUSAL  # canned answers live in config/prompts.yaml

log = logging.getLogger(__name__)


class BotState(TypedDict, total=False):
    input: str              # raw user message
    chat_history: list      # (role, content) tuples or BaseMessages
    question: str           # input rewritten as a standalone question
    topics: list[str]       # predefined topics matched by classify
    disclaimers: list[str]  # "a"-topic texts to append after the agent answer
    answer: str             # final response (canned, agent, REFUSAL, REJECT, or a before_agent node's)
    followups: list[str]    # suggested follow-up questions (empty when none needed)
    sources: dict           # {sources_key: [{title, link, type}]} — distinct documents behind doc_search_tool,
                            # plus {"predefined": [...]} for the predefined responses shown (link may be "")
    sources_md: str         # the same as a markdown list, ready to show under the answer
    kg: list                # knowledge graphs attached to tool results, one per tool call
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
    type}]} (by a <config dir>/interceptors.py interceptor, e.g. the studies a catalog search cites),
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


SLOTS = ("before_agent", "after_agent", "after_finish")  # where the config's nodes go (see the diagram)
BUILT_IN = ("contextualize", "input_guardrail", "classify", "main_agent", "output_guardrail", "suggest_followups",
            "append_disclaimer")  # the built-in steps a client sees start (gate and finish are instant)
DEFAULT_NODE_TIMEOUT = 30  # seconds, for a slot project.yaml's node_timeout doesn't set


def load_nodes() -> dict:
    """project.yaml's `extra_nodes: {slot: {name: settings}}` (slots: SLOTS, each in order), each name
    a function in <config dir>/nodes.py: build_graph's nodes, {slot: [(name, factory, settings)]}. A
    factory is (llm, tools, settings) -> async node(state) -> state update, or None for no step (e.g.
    its tool is missing); see examples/bdc/nodes.py. settings, the node's own mapping (empty if
    none): "label", what a web client shows while it runs; "fields", state fields of its own (passed
    along, returned with the answer); and anything else the node reads (e.g. the name of a tool).
    An unknown slot, a name missing from nodes.py, settings that aren't a mapping or fields that aren't
    a list of names fail startup, and so do two after_agent nodes listing one field, or one listing a
    BotState field (they run side by side, with each other and with steps writing those), and an
    unknown slot in node_timeout ({slot: seconds})."""
    wanted = prompts.PROJECT.get("extra_nodes") or {}
    if (not isinstance(wanted, dict) or set(wanted) - set(SLOTS)
            or not all(isinstance(names or {}, dict) for names in wanted.values())):
        raise ValueError(f"project.yaml [extra_nodes]: a mapping of {', '.join(SLOTS)} to {{name: settings}}, "
                         f"not {wanted!r}")
    timeouts = prompts.NODE_TIMEOUT
    if not isinstance(timeouts, dict) or set(timeouts) - set(SLOTS):
        raise ValueError(f"project.yaml [node_timeout]: a mapping of {', '.join(SLOTS)} to seconds, not {timeouts!r}")
    if not any(wanted.values()):
        return {}
    path = prompts.DATA_DIR / "nodes.py"
    spec = importlib.util.spec_from_file_location("config_nodes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # a missing file fails startup here
    nodes = {slot: [(name, getattr(module, name, None), settings or {})
                    for name, settings in (wanted.get(slot) or {}).items()] for slot in SLOTS}
    for slot, entries in nodes.items():
        for name, factory, settings in entries:
            fields = settings.get("fields", []) if isinstance(settings, dict) else None
            if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
                raise ValueError(f"project.yaml [extra_nodes.{slot}.{name}]: a mapping (label, fields: a list of "
                                 f"names, the node's own settings), not {settings!r}")
            if not callable(factory):
                raise ValueError(f"project.yaml [extra_nodes]: no function {name} in {path}")
    owner = {}  # field -> the after_agent node listing it
    for name, _, settings in nodes["after_agent"]:
        for f in settings.get("fields", []):
            if f in BotState.__annotations__ or f in owner:
                writer = f"{owner[f]} lists it too" if f in owner else "it is the workflow's own"
                raise ValueError(f"project.yaml [extra_nodes.after_agent.{name}]: can't write field {f!r}: {writer}, "
                                 "and after_agent nodes run side by side with the steps writing those")
            owner[f] = name
    return nodes


def builtin_labels() -> dict:
    """project.yaml's `builtin_nodes: {step: {label: text}}`: what a web client shows while a BUILT_IN
    step runs (its own text for one not set). Another step or setting fails startup."""
    wanted = prompts.PROJECT.get("builtin_nodes") or {}
    if (not isinstance(wanted, dict) or set(wanted) - set(BUILT_IN)
            or any(not isinstance(s or {}, dict) or set(s or {}) - {"label"} for s in wanted.values())):
        raise ValueError(f"project.yaml [builtin_nodes]: a mapping of {', '.join(BUILT_IN)} to {{label: text}}, "
                         f"not {wanted!r}")
    return {name: s["label"] for name, s in wanted.items() if (s or {}).get("label")}


def node_fields(nodes) -> list[str]:
    """Every field the config's nodes list in their settings, each once: build_graph adds them to the
    state, api.py returns them with the answer."""
    return list(dict.fromkeys(f for entries in (nodes or {}).values() for _, _, s in entries
                              for f in s.get("fields", [])))


def build_graph(llm, agent, predefined: dict, tools=(), nodes=None):
    """Compile the workflow.

    llm: any chat model — runs the guardrail/contextualize/classify prompts.
    agent: has astream({'messages': [...]}, stream_mode=["messages", "values"]) —
        produces the real answer, token-streamable.
    predefined: lowercased topic → {response, flag, link?, title?}, from config/predefined_responses.yaml.
        flag "r" = replace: the canned response IS the answer, agent is skipped.
        flag "a" = append: the response is a disclaimer added after the agent answer.
    tools: the agent's tools, handed to the config nodes' factories.
    nodes: load_nodes()'s {slot: [(name, factory, settings)]}. A before_agent
        node that returns an answer ends the run with it (no agent, guardrail, follow-ups or
        disclaimers; sources {} unless it returns its own). An after_agent node returns only the
        fields its settings list (other keys are logged and dropped); a node that raises, or takes over its
        slot's prompts.NODE_TIMEOUT seconds (DEFAULT_NODE_TIMEOUT if unset), is logged and its update dropped.
        Their settings' "fields" join the state; a node's update keeps only state fields (others are
        logged and dropped).
    Each step's start goes on the custom stream as {"type": "node", "node", "label"?}: label is a
        config node's "label" setting, or a built-in step's (builtin_labels), when set.
    prompts.FOLLOWUPS 0 leaves out suggest_followups, prompts.APPEND_DISCLAIMER False append_disclaimer
        ("a"-topic texts then wait in "disclaimers" for an after_finish node of the config's own).
    """
    topic_names = list(predefined)
    own = [f for f in node_fields(nodes) if f not in BotState.__annotations__]
    State = TypedDict("State", {**BotState.__annotations__, **{f: Any for f in own}}, total=False)
    # each with the fields it may write side by side (after_agent): its own, none of BotState's
    extras = {slot: [(name, node, set(settings.get("fields", [])) - set(BotState.__annotations__))
                     for name, f, settings in (nodes or {}).get(slot, ()) if (node := f(llm, tools, settings))]
              for slot in SLOTS}
    after_fields = [f for _, _, fields in extras["after_agent"] for f in fields]  # what a rejected answer drops
    labels = builtin_labels() | {name: s["label"] for entries in (nodes or {}).values()
                                 for name, _, s in entries if s.get("label")}

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
        # e.g. what an interceptor attached (the studies a catalog search cites), then classify's "a" topics
        extra = _attached_sources(result["messages"]) | state.get("sources", {})
        sources = ({prompts.SOURCES_KEY: docs} if docs else {}) | extra
        sources_md = _sources_md(docs + [s for items in extra.values() for s in items])
        kg = _kgs(result["messages"])
        # the agent's sources are known now: send them rather than wait for "done", which waits for
        # the guardrail, follow-up and extra nodes (a block clears them in "done"; an after_finish node
        # may add to them)
        writer({"type": "sources", "sources": sources, "sources_md": sources_md, "kg": kg})
        return {"answer": result["messages"][-1].text, "sources": sources, "sources_md": sources_md, "kg": kg}

    async def output_guardrail(state: BotState):
        """
        LLM self-check that the answer addresses the question; anything but "yes"
        → replace the streamed answer with REJECT (its sources and kg go with it;
        finish drops the follow-ups, and no disclaimers follow).
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
        Join of output_guardrail, suggest_followups and the after_agent nodes, which run side by
        side: a rejected answer gets no follow-ups, nor what the after_agent nodes made of it (their
        fields, null); one that passed goes on to the after_finish nodes and, with "a"-topic
        disclaimers, append_disclaimer.
        """
        return {"followups": [], **dict.fromkeys(after_fields)} if state["blocked"] else {}

    async def append_disclaimer(state: BotState):
        """
        Add the queued "a"-topic disclaimers to an answer that passed the output check.
        """
        return {"answer": "\n\n".join([state["answer"], *state["disclaimers"]])}

    def announce(name, fn):
        # emit {"type": "node"} on the custom stream when the node starts, so a
        # streaming client can show progress; no-op writer under plain ainvoke
        async def wrapped(state: State):  # LangGraph hands a node only the fields its hint names
            label = {"label": labels[name]} if name in labels else {}
            get_stream_writer()({"type": "node", "node": name, **label})
            return await fn(state)
        return wrapped

    def extra(name, fn, slot, fields):
        keep = fields if slot == "after_agent" else None
        limit = float(prompts.NODE_TIMEOUT.get(slot, DEFAULT_NODE_TIMEOUT))

        async def guarded(state: BotState):
            try:
                # a hung node would hold up the answer: "done" waits on every step
                update = await asyncio.wait_for(fn(state), limit) or {}
            except Exception as e:
                # extras, like follow-ups — never lose a good answer over one; but say why, or a bad
                # tool argument or prompt placeholder turns the step off unseen
                log.warning("%s skipped: %s: %s", name, type(e).__name__, e)
                return {}
            if unknown := set(update) - set(State.__annotations__):
                # LangGraph would drop them unseen
                log.warning("%s: dropped %s (not a state field: list it in the node's fields in project.yaml)",
                            name, ", ".join(sorted(unknown)))
                update = {k: v for k, v in update.items() if k not in unknown}
            if keep is not None and (dropped := set(update) - keep):
                # side by side with other steps, a key one of them writes too would fail the whole chat
                log.warning("%s: dropped %s (an after_agent node returns only the fields it lists)",
                            name, ", ".join(sorted(dropped)))
                update = {k: v for k, v in update.items() if k in keep}
            if slot == "before_agent" and update.get("answer"):
                # the run ends here: classify's "a"-topic sources went with disclaimers it won't get
                update = {"sources": {}, "sources_md": "", **update}
            return update
        return guarded

    # Build the graph.
    g = StateGraph(State)
    before, after_finish = ([(name, extra(name, fn, slot, fields)) for name, fn, fields in extras[slot]]
                            for slot in ("before_agent", "after_finish"))
    after_agent = [("output_guardrail", output_guardrail),
                   *([("suggest_followups", suggest_followups)] if prompts.FOLLOWUPS > 0 else []),
                   *((name, extra(name, fn, "after_agent", fields)) for name, fn, fields in extras["after_agent"])]
    disclaimer = [("append_disclaimer", append_disclaimer)] if prompts.APPEND_DISCLAIMER else []
    for name, fn in [("input_guardrail", input_guardrail), ("contextualize", contextualize),
                     ("classify", classify), *before, ("main_agent", run_agent), *after_agent,
                     *after_finish, *disclaimer]:
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
    chain = [name for name, _ in before] + ["main_agent"]  # before_agent nodes, in order
    g.add_conditional_edges("gate",
                            lambda s: "blocked or canned" if s.get("answer") else "regular",
                            {"blocked or canned": END, "regular": chain[0]})
    for a, b in zip(chain, chain[1:]):  # likewise, a before_agent node's answer ends the run
        g.add_conditional_edges(a, lambda s: "answered" if s.get("answer") else "go on", {"answered": END, "go on": b})
    for name, _ in after_agent:
        g.add_edge("main_agent", name)
    g.add_edge([name for name, _ in after_agent], "finish")  # waits for all of them
    last = "finish"
    if after_finish:  # in order, on an answer that passed: a blocked one is the canned REJECT
        g.add_conditional_edges("finish", lambda s: "blocked" if s["blocked"] else "passed",
                                {"blocked": END, "passed": after_finish[0][0]})
        for (a, _), (b, _) in zip(after_finish, after_finish[1:]):
            g.add_edge(a, b)
        last = after_finish[-1][0]
    if disclaimer:
        # one key per target, as at gate: a drawing shows one edge to END, so a separate "rejected" key would vanish
        done = "done or blocked" if last == "finish" else "done"
        g.add_conditional_edges(last, lambda s: "disclaimer" if not s["blocked"] and s.get("disclaimers") else done,
                                {"disclaimer": "append_disclaimer", done: END})
        g.add_edge("append_disclaimer", END)
    else:
        g.add_edge(last, END)
    return g.compile()


def drawing(graph, agent, servers: dict) -> str:
    """For docs/workflow_*_detailed.png, as mermaid text: graph.get_graph() with the main_agent node
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
