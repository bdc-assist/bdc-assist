"""The bdc-assist LangGraph workflow.

    START → input_guardrail ──blocked──────────────────────→ END (REFUSAL)
                │ ok
            contextualize → classify ──"r" topic matched───→ END (canned answer)
                                │ regular
                              agent → output_guardrail ──rejected──→ output_reject ─→ END (REJECT)
                                            │ disclaimers queued └──done─────→ END
                                      append_disclaimer ───→ END
"""

from typing import TypedDict

from langchain_core.output_parsers import MarkdownListOutputParser
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
    blocked: bool           # input_guardrail verdict
    rejected: bool          # output_guardrail rejected the agent answer


def build_graph(llm, agent, predefined: dict):
    """Compile the workflow.

    llm: any chat model — runs the guardrail/contextualize/classify prompts.
    agent: has ainvoke({'messages': [...]}) — produces the real answer.
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
        Answer the question with the agent.
        """
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": state["question"]}]}
        )
        return {"answer": result["messages"][-1].content}

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
        Replace a rejected answer with the canned REJECT reply.
        """
        return {"answer": REJECT}

    async def append_disclaimer(state: BotState):
        """
        Tack queued "a"-topic disclaimers onto the final answer.
        """
        return {"answer": "\n\n".join([state["answer"], *state["disclaimers"]])}

    # Build the graph.
    g = StateGraph(BotState)
    g.add_node("input_guardrail", input_guardrail)
    g.add_node("contextualize", contextualize)
    g.add_node("classify", classify)
    g.add_node("agent", run_agent)
    g.add_node("output_guardrail", output_guardrail)
    g.add_node("output_reject", output_reject)
    g.add_node("append_disclaimer", append_disclaimer)

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
                            {"rejected": "output_reject", "disclaimer": "append_disclaimer", "done": END})
    g.add_edge("output_reject", END)
    g.add_edge("append_disclaimer", END)
    return g.compile()
