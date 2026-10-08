import asyncio
import importlib.util
import json
from pathlib import Path

from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from mcp.types import CallToolResult, TextContent, Tool

from r_assist.agent import load_interceptors

# a dug-mcp search_concepts reply (find_variables=true), cut to the fields that matter
SEARCH_CONCEPTS = {
    "search_term": "heart attack",
    "enrichment": {"curies": ["MONDO:0005068"], "labels": ["myocardial infarction"]},
    "total_results": 1,
    "variables": [{
        "variable_id": "phv001.v1.p1", "variable_name": "MI_EVER", "variable_description": "Ever had an MI",
        "matched_concepts": [
            {"concept_id": "MONDO:0005068", "concept_name": "myocardial infarction",
             "concept_type": "biolink:Disease", "predicate": "biolink:related_to"},
            {"concept_id": "HP:0001658", "concept_name": "Myocardial infarction",
             "concept_type": "biolink:PhenotypicFeature", "predicate": None},
        ],
    }],
}
# get_concept_graph at expand_depth 2: one row with a study, one without (OPTIONAL MATCH missed)
CONCEPT_GRAPH = {"concept_id": "MONDO:0005068", "expand_depth": 2, "total_results": 2, "graph": [
    {"concept": "myocardial infarction", "concept_id": "MONDO:0005068", "concept_type": "biolink.Disease",
     "variable_name": "MI_EVER", "variable_id": "phv001", "study_name": "Framingham", "study_id": "phs000007",
     "related_concepts_count": 3},
    {"concept": "myocardial infarction", "concept_id": "MONDO:0005068", "concept_type": "biolink.Disease",
     "variable_name": "MI_AGE", "variable_id": "phv002", "study_name": None, "study_id": None,
     "related_concepts_count": 1},
]}


class FakeSession:
    def __init__(self, text):
        self.text = text

    async def call_tool(self, name, args, progress_callback=None):
        return CallToolResult(content=[TextContent(type="text", text=self.text)])


def call(tool_name, reply, server="dug_mcp"):
    """One call through the real adapter + the BDC interceptors -> the ToolMessage the agent gets."""
    text = reply if isinstance(reply, str) else json.dumps(reply)
    tool = convert_mcp_tool_to_langchain_tool(
        FakeSession(text), Tool(name=tool_name, inputSchema={"type": "object"}),
        tool_interceptors=load_interceptors(Path("examples/bdc")), server_name=server)
    msg = asyncio.run(tool.ainvoke({"type": "tool_call", "name": tool_name, "args": {"q": 1}, "id": "c1"}))
    assert msg.content[0]["text"] == text, "the LLM-visible text is never changed"
    return (msg.artifact or {}).get("structured_content", {}).get("kg")


def test_dug_results_get_a_trimmed_kg():
    assert call("search_concepts", SEARCH_CONCEPTS) == {
        "tool": "search_concepts", "args": {"q": 1}, "label": "search concepts",  # no search_term in these args
        "nodes": [{"id": "phv001.v1.p1", "name": "MI_EVER", "type": "variable", "description": "Ever had an MI"},
                  {"id": "MONDO:0005068", "name": "myocardial infarction", "type": "concept",
                   "category": "biolink:Disease"},  # Dug's category, verbatim
                  {"id": "HP:0001658", "name": "Myocardial infarction", "type": "concept",
                   "category": "biolink:PhenotypicFeature"}],
        "edges": [{"subject": "phv001.v1.p1", "object": "MONDO:0005068", "predicate": "related_to"},
                  {"subject": "phv001.v1.p1", "object": "HP:0001658"}]}

    kg = call("get_concept_graph", CONCEPT_GRAPH)
    assert [(n["id"], n["type"]) for n in kg["nodes"]] == [
        ("phv001", "variable"), ("MONDO:0005068", "concept"), ("phs000007", "study"), ("phv002", "variable")]
    assert kg["nodes"][1]["category"] == "biolink.Disease"  # verbatim, dot and all
    # variables keep related_concepts_count, under attributes
    assert kg["nodes"][0]["attributes"] == {"related_concepts_count": 3}
    assert kg["nodes"][3]["attributes"] == {"related_concepts_count": 1}
    assert "attributes" not in kg["nodes"][1]  # nothing to keep for the concept
    assert kg["edges"] == [{"subject": "phv001", "object": "MONDO:0005068"},
                           {"subject": "phv001", "object": "phs000007"},
                           {"subject": "phv002", "object": "MONDO:0005068"}]


# made-up rows in each tool's shape (dug-mcp redismcp_server.py): find_cohort_variables nests them
# under studies keyed variable_id/study_id, picsure_search lists them flat keyed phv_id/study
COHORT = {"feasible_studies": [{"study_id": "s1", "variables": [
    {"variable_id": "v1", "study_id": "s1", "matched_concepts": ["c1"]}]}]}
PICSURE = {"variables": [{"phv_id": "v1", "study": "s1", "matched_concepts": [{"concept_id": "c1"}]}]}
# get_concept_graph at expand_depth 1 and get_concept_connections: neighbour rows keyed connected_*
NEIGHBOURS = {"concept_id": "c1", "graph": [{"connected_id": "c2", "connected_type": "biolink.Disease"}]}
CONNECTIONS = {"concept_id": "c1", "connections": [{"connected_id": "c2", "relationship": "biolink:related_to"}]}


def test_each_graph_tool_gets_a_kg():
    """Each call's graph comes back labelled with the tool that was called. find_cohort_variables
    rows drew nothing while the interceptor read only picsure_search's field names."""
    for tool, reply in [("find_cohort_variables", COHORT), ("picsure_search", PICSURE),
                        ("get_concept_graph", NEIGHBOURS), ("get_concept_connections", CONNECTIONS)]:
        kg = call(tool, reply)
        assert kg and kg["tool"] == tool and kg["edges"], tool


def test_each_node_gets_its_role():
    """type: concept, variable, study, or term (find_cohort_variables' search word); Dug's
    own Study/StudyVariable labels on neighbours count too."""
    types = lambda kg: {n["id"]: n["type"] for n in kg["nodes"]}
    assert types(call("find_cohort_variables", COHORT)) == {"v1": "variable", "s1": "study", "c1": "term"}
    neighbours = {"concept_id": "c1", "connections": [
        {"connected_id": "c2", "connected_type": "biolink.Disease"},
        {"connected_id": "s9", "connected_type": "biolink.Study"},
        {"connected_id": "v9", "connected_type": "biolink.StudyVariable"}]}
    assert types(call("get_concept_connections", neighbours)) == {
        "c1": "concept", "c2": "concept", "s9": "study", "v9": "variable"}


def test_each_graph_is_labelled_in_plain_words():
    spec = importlib.util.spec_from_file_location("bdc_interceptors", "examples/bdc/interceptors.py")
    bdc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bdc)

    def label(tool, reply, args):
        return bdc.label(tool, args, bdc.to_kg(tool, reply))

    assert label("get_concept_graph", CONCEPT_GRAPH, {"concept_id": "MONDO:0005068"}) == "myocardial infarction concept graph"
    assert label("find_cohort_variables", COHORT, {"concepts": ["asthma", "copd"]}) == "asthma + copd cohort variables"
    assert label("search_concepts", SEARCH_CONCEPTS, {"search_term": "heart attack"}) == "heart attack concept search"
    # dug doesn't name the concept get_concept_connections was asked about: its id
    assert label("get_concept_connections", CONNECTIONS, {"concept_id": "c1"}) == "c1 related concepts"
    assert label("find_cohort_variables", COHORT, {}) == "find cohort variables"  # nothing to name it by
    assert call("get_concept_graph", CONCEPT_GRAPH)["label"]  # the interceptor attaches it


def test_no_kg_when_nothing_to_draw():
    assert call("search_concepts", SEARCH_CONCEPTS, server="r_doc_mcp") is None  # yaml scopes dug_kg to dug_mcp
    assert call("list_graph_schema", {"schema": [{"node_type": "biolink.Disease", "count": 9}]}) is None  # no edges
    assert call("search_concepts", json.dumps(SEARCH_CONCEPTS)[:80] + "\n... (truncated)") is None  # dug's 50k cap
    assert load_interceptors(Path("config")) == []  # the template names none


def test_unknown_interceptor_fails_at_startup(tmp_path):
    (tmp_path / "mcp_servers.yaml").write_text("s:\n  transport: sse\n  url: http://x\n  interceptors: [nope]\n")
    (tmp_path / "interceptors.py").write_text("async def dug_kg(request, handler): ...\n")
    try:
        load_interceptors(tmp_path)
    except ValueError as e:
        assert "mcp_servers.yaml [s]: no function nope" in str(e)
    else:
        raise AssertionError("a misspelt interceptor name must not be silently skipped")
