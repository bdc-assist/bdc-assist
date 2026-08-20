"""Loads prompt texts from data/prompts.yaml (kept there for non-technical review).

{topics} and {date} are filled here; {input}/{answer} at the call sites in graph.py.
"""

import datetime
import re
from pathlib import Path

import yaml

_DATA = Path(__file__).resolve().parent.parent / "data"

with open(_DATA / "prompts.yaml", encoding="utf-8") as f:
    _P = yaml.safe_load(f)

INPUT_GUARDRAIL_SYSTEM = _P["input_guardrail_system"]
INPUT_GUARDRAIL_HUMAN = _P["input_guardrail_human"]
CONTEXTUALIZE_SYSTEM = _P["contextualize_system"]
OUTPUT_GUARDRAIL_HUMAN = _P["output_guardrail_human"]
REFUSAL = _P["refusal"]
REJECT = _P["reject"]


def topic_classifier_system(topics: list[str]) -> str:
    return _P["topic_classifier_system"].format(topics=", ".join(f'"{t}"' for t in topics))


def agent_system() -> str:
    # date lives here because small models ignore date rules in tool docstrings
    return _P["agent_system"].format(date=datetime.date.today().isoformat())


def normalize_bdc_names(text: str) -> str:
    """Deterministic pass: strip '(BDC)' parentheticals, replace full names with 'BDC'."""

    def remove(match):
        pre = " " if match.group("pre") else ""
        post = " " if match.group("post") else ""
        return " " if pre and post else pre + post

    def replace(match):
        pre = " " if match.group("pre") else ""
        post = " " if match.group("post") else ""
        return " BDC " if pre and post else pre + "BDC" + post

    text = re.sub(
        r"(?P<pre>\s*)\(\s*(?:(?:NHLBI\s+)?BioData\s+Catalyst(?:®️?)?|BDC)\s*\)(?P<post>\s*)",
        remove, text)
    text = re.sub(
        r"(?P<pre>\s*)(?:NHLBI\s+)?BioData\s+Catalyst(?:®️?)?(?P<post>\s*)",
        replace, text)
    return text


def load_predefined_responses(path=None) -> dict:
    with open(path or _DATA / "predefined_responses.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {k.lower(): v for k, v in data.items()}
