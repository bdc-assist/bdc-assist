"""prompts.yaml is a ${placeholder} template filled from project.yaml at import."""
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from r_assist import config, prompts

ROOT = Path(__file__).resolve().parent.parent


def _reload(data_dir):
    os.environ["CONFIG_DIR"] = str(data_dir)
    importlib.reload(config)  # CONFIG_DIR is read once in config.py
    return importlib.reload(prompts)


@pytest.fixture(autouse=True)
def _restore_default_data_dir():
    yield
    os.environ.pop("CONFIG_DIR", None)
    importlib.reload(config)
    importlib.reload(prompts)


def test_template_fills_from_project_yaml():
    p = _reload(ROOT / "config")
    texts = [p.INPUT_GUARDRAIL_SYSTEM, p.INPUT_GUARDRAIL_HUMAN, p.CONTEXTUALIZE_SYSTEM, p.OUTPUT_GUARDRAIL_HUMAN,
             p.SUGGEST_FOLLOWUPS_HUMAN, p.REFUSAL, p.REJECT, p.SOURCES, p.SOURCES_ITEM,
             p.agent_system(), p.topic_classifier_system(["a", "b"])]
    assert not any("${" in t for t in texts), "every placeholder must be filled"
    assert "My Project (MP)" in p.INPUT_GUARDRAIL_SYSTEM
    assert 'called "MP Assist"' in p.agent_system() and "Today is 20" in p.agent_system()
    assert '"a", "b"' in p.topic_classifier_system(["a", "b"])
    assert p.load_predefined_responses() == {}, "the template ships with no topics (comments only)"
    assert (p.DOC_SEARCH_TOOL, p.SOURCES_KEY) == ("search_docs", "r-doc")
    assert _reload(ROOT / "examples" / "bdc").SOURCES_KEY == "bdc-doc"


def test_bdc_example_loads_and_fills_predefined():
    p = _reload(ROOT / "examples" / "bdc")
    assert 'called "BDC Assist"' in p.agent_system()
    assert "NHLBI BioData Catalyst" in p.REFUSAL or "BDC" in p.REFUSAL
    predefined = p.load_predefined_responses()
    assert predefined["fisma"]["flag"] == "r" and "${" not in predefined["fisma"]["response"]
    assert predefined["fisma"]["title"] == "FISMA Policy" and predefined["fisma"]["link"].startswith("https://")
    assert predefined["covid"]["link"] == "", "link may be empty"


def test_unknown_placeholder_fails_loudly(tmp_path):
    (tmp_path / "project.yaml").write_text("name: X\nshort_name: X\nassistant_name: X\n", encoding="utf-8")
    (tmp_path / "prompts.yaml").write_text("refusal: hello ${nope}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"\$\{nope\}"):
        _reload(tmp_path)


def test_prompts_import_loads_dotenv_first():
    # CONFIG_DIR may live in .env; config.py reads it right after load_dotenv(), before prompts.py uses it
    code = "import sys, r_assist.prompts; assert 'r_assist.config' in sys.modules, 'config not imported before prompts read the env'"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr


def test_agent_prompt_has_failure_rules():
    from r_assist import prompts

    text = prompts.agent_system()
    assert "Never conclude from a single empty result" in text
    assert "currently unavailable" in text and "do not guess" in text
    assert "NEVER invent placeholder text" in text
    assert "counts as helpful" in prompts.OUTPUT_GUARDRAIL_HUMAN


def test_agent_system_takes_server_prompts_from_mcp_servers_yaml(tmp_path):
    bdc = ROOT / "examples" / "bdc"
    text = _reload(bdc).agent_system()
    assert "search_docs searches" in text and "search_concepts" in text and "{tools}" not in text
    assert text.index("search_docs searches") < text.index("search_concepts"), "mcp_servers.yaml order"
    assert 'answering. \n<tools server="r_doc_mcp">\n- search_docs' in text, "each server's prompt in its own tag"
    assert 'not catalog results), and do not guess.\n</tools>\nNever conclude' in text
    for f in ("project.yaml", "prompts.yaml"):
        (tmp_path / f).write_text((bdc / f).read_text(encoding="utf-8"), encoding="utf-8")
    servers = yaml.safe_load((bdc / "mcp_servers.yaml").read_text(encoding="utf-8"))
    (tmp_path / "mcp_servers.yaml").write_text(yaml.safe_dump({"r_doc_mcp": servers["r_doc_mcp"]}), encoding="utf-8")
    text = _reload(tmp_path).agent_system()
    assert "search_docs searches" in text and "search_concepts" not in text, "dug_mcp's prompt leaves with it"
    (tmp_path / "mcp_servers.yaml").write_text("r_doc_mcp: {transport: sse}\n", encoding="utf-8")
    text = _reload(tmp_path).agent_system()
    assert "You have these tools" not in text and "{tools}" not in text, "no prompt: -> no tools line"
    assert "not other organizations.\nNever conclude" in text, "and no blank line left behind"


def test_followups_count_comes_from_project_yaml():
    p = _reload(ROOT / "config")
    assert p.FOLLOWUPS == 3
    assert "exactly 3 short follow-up questions" in p.SUGGEST_FOLLOWUPS_HUMAN, "${followups} filled from project.yaml"
    assert _reload(ROOT / "examples" / "bdc").FOLLOWUPS == 3, "matches the verbatim BDC prompts"
