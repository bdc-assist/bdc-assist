"""Loads the prompt texts from <config dir>/prompts.yaml (kept there for non-technical review).

prompts.yaml is a template: ${name}, ${short_name}, ${assistant_name}, ${followups} come from project.yaml
in the same folder and are filled here at import. {input}/{answer}/{topics}/{date}/{tools} are
filled at the call sites (graph.py and the functions below).
CONFIG_DIR (default ./config) picks the folder, e.g. examples/bdc.
"""

import datetime
import re
from string import Template

import yaml

from .config import CONFIG_DIR

DATA_DIR = CONFIG_DIR  # every yaml below is read from this folder


class _UniqueKeys(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    # plain YAML keeps the last of a key written twice, silently: e.g. a second after_agent: under
    # extra_nodes would drop the first one's nodes
    keys = [loader.construct_object(k, deep=deep) for k, _ in node.value]
    if dup := next((k for k in keys if keys.count(k) > 1), None):
        raise yaml.constructor.ConstructorError(None, None, f"{dup!r} written twice: give it once, with "
                                                "everything under it", node.start_mark)
    return loader.construct_mapping(node, deep)


_UniqueKeys.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_yaml(f):
    """yaml.safe_load, but a key twice in one mapping fails (with its file and line)."""
    return yaml.load(f, Loader=_UniqueKeys)


def _load_yaml(name):
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return load_yaml(f) or {}


PROJECT = _load_yaml("project.yaml")  # name, short_name, assistant_name, followups, doc_search_tool, sources_key,
                                      # append_disclaimer, extra_nodes (graph.load_nodes), node_timeout
FOLLOWUPS = int(PROJECT.get("followups", 3))  # how many follow-ups to ask for and to keep; 0 = no suggest_followups step
APPEND_DISCLAIMER = bool(PROJECT.get("append_disclaimer", True))  # False = no append_disclaimer step
NODE_TIMEOUT = PROJECT.get("node_timeout") or {}  # {slot: seconds} a node of the config's own may take, then
                                                  # skipped; graph.DEFAULT_NODE_TIMEOUT for a slot not set
DOC_SEARCH_TOOL = PROJECT.get("doc_search_tool", "search_docs")  # its results become the answer's sources
SOURCES_KEY = PROJECT.get("sources_key", "r-doc")  # key of those sources in the API response


def _fill(text, where):
    filled = Template(text).safe_substitute(PROJECT)
    left = re.search(r"\$\{[^}]*\}?", filled)
    if left:
        raise ValueError(f"{where}: unknown placeholder {left.group()} — define it in {DATA_DIR / 'project.yaml'}")
    return filled


PROMPTS = {k: _fill(v, f"prompts.yaml [{k}]") for k, v in _load_yaml("prompts.yaml").items()}  # extra nodes read theirs here

INPUT_GUARDRAIL_SYSTEM = PROMPTS["input_guardrail_system"]
INPUT_GUARDRAIL_HUMAN = PROMPTS["input_guardrail_human"]
CONTEXTUALIZE_SYSTEM = PROMPTS["contextualize_system"]
OUTPUT_GUARDRAIL_HUMAN = PROMPTS["output_guardrail_human"]
SUGGEST_FOLLOWUPS_HUMAN = PROMPTS["suggest_followups_human"]
REFUSAL = PROMPTS["refusal"]
REJECT = PROMPTS["reject"]
SOURCES = PROMPTS["sources"]  # {items}
SOURCES_ITEM = PROMPTS["sources_item"]  # {title}, {link}, {type}
SOURCES_ITEM_NO_LINK = PROMPTS["sources_item_no_link"]  # {title}, {type}: a predefined response with an empty link


def topic_classifier_system(topics: list[str]) -> str:
    return PROMPTS["topic_classifier_system"].format(topics=", ".join(f'"{t}"' for t in topics))


def agent_system() -> str:
    """{tools} = each server's `prompt:` in mcp_servers.yaml (how to use its tools), in that file's order,
    each in a <tools server="..."> tag so its rules read as that server's only."""
    tools = "\n".join(f'<tools server="{name}">\n{_fill(conn["prompt"], f"mcp_servers.yaml [{name}.prompt]").rstrip()}\n</tools>'
                      for name, conn in _load_yaml("mcp_servers.yaml").items() if conn.get("prompt"))
    text = PROMPTS["agent_system"]
    if tools and "{tools}" not in text:
        raise ValueError("prompts.yaml [agent_system]: no {tools} slot for the servers' prompt: texts")
    if not tools:  # no tool guidance: drop the whole {tools} line, its intro included
        text = re.sub(r"[^\n]*\{tools\}[^\n]*\n?", "", text)
    # date lives here because small models ignore date rules in tool docstrings
    return text.format(date=datetime.date.today().isoformat(), tools="\n" + tools)


def load_predefined_responses(path=None) -> dict:
    """lowercased topic -> {response, flag, link, title}; responses may use the ${...} placeholders too.
    title defaults to the topic as written; link may be empty."""
    with open(path or DATA_DIR / "predefined_responses.yaml", encoding="utf-8") as f:
        data = load_yaml(f) or {}
    return {k.lower(): {**v, "title": str(v.get("title") or k), "link": str(v.get("link") or ""),
                        "response": _fill(str(v["response"]), f"predefined_responses.yaml [{k}]")}
            for k, v in data.items()}
