"""Tool-call interceptors for the BDC bot. mcp_servers.yaml names them per server
(`interceptors: [dug_kg]`); r-assist runs each on that server's tool calls only, as a
langchain-mcp-adapters tool interceptor: async (request, handler) -> result.

dug_kg turns a graph-shaped dug-mcp result into a small knowledge graph, trimmed to what
a graph view draws, and attaches it as the result's structuredContent["kg"]. The agent's
LLM still reads only the text; r-assist lifts every attached kg into the response's "kg"
list. dug-mcp itself is unchanged (github.com/bdc-assist/dug-mcp, redismcp_server.py).
"""

import json

from mcp.types import CallToolResult


def _short(biolink):
    # "biolink.Disease" (RedisGraph label) / "biolink:related_to" -> "Disease" / "related_to"
    return (biolink or "").replace("biolink.", "").replace("biolink:", "") or None


def _role(category):
    """A neighbour's type from its Dug category: dug-mcp labels its own study and variable
    nodes Study / StudyVariable; everything else is a concept."""
    return {"Study": "study", "StudyVariable": "variable"}.get(_short(category), "concept")


def to_kg(tool: str, data: dict) -> dict | None:
    """One decoded dug-mcp result -> {"nodes": [{id, name, type, category?, description?, attributes?}],
    "edges": [{subject, object, predicate?}]}, or None when its rows hold no edges
    (counts, schema, name lists, errors). `type` is the node's role: concept, variable,
    study, or term (a search word standing in for a concept: find_cohort_variables).
    `category` is Dug's own, verbatim, and only where Dug gives one. `attributes` holds
    the other fields worth keeping (variables: related_concepts_count). Every other field
    is dropped."""
    nodes, edges = {}, {}

    def node(id, name=None, type="concept", category=None, description=None, attributes=None):
        if id and id not in nodes:
            attributes = {k: v for k, v in (attributes or {}).items() if v is not None}
            n = {"id": id, "name": name or id, "type": type, "category": category, "description": description,
                 "attributes": attributes}
            nodes[id] = {k: v for k, v in n.items() if v}
        return id

    def edge(subject, object, predicate=None):
        if subject and object:
            e = {"subject": subject, "object": object, "predicate": _short(predicate)}
            edges[(subject, object, e["predicate"])] = {k: v for k, v in e.items() if v}

    if tool == "search_concepts":  # find_variables=true: variable -[predicate]- each matched concept
        for v in data.get("variables", []):
            var = node(v.get("variable_id"), v.get("variable_name"), "variable", description=v.get("variable_description"))
            for c in v.get("matched_concepts", []):
                concept = node(c.get("concept_id"), c.get("concept_name"), category=c.get("concept_type"))
                edge(var, concept, c.get("predicate"))
    elif tool == "get_concept_graph":
        for r in data.get("graph", []):
            if "variable_id" in r:  # expand_depth >= 2: concept - variable - study
                var = node(r["variable_id"], r.get("variable_name"), "variable",
                           attributes={"related_concepts_count": r.get("related_concepts_count")})
                edge(var, node(r.get("concept_id"), r.get("concept"), category=r.get("concept_type")))
                edge(var, node(r.get("study_id"), r.get("study_name"), "study"))
            else:  # expand_depth 1: concept -[rel_type]- each neighbour
                category = r.get("connected_type")
                edge(node(data.get("concept_id"), r.get("concept")),
                     node(r.get("connected_id"), r.get("connected_name"), _role(category), category), r.get("rel_type"))
    elif tool == "get_concept_connections":
        for r in data.get("connections", []):
            category = r.get("connected_type")
            edge(node(data.get("concept_id")),
                 node(r.get("connected_id"), r.get("connected_name"), _role(category), category), r.get("relationship"))
    elif tool in ("picsure_search", "find_cohort_variables"):  # study - variable - matched concepts
        studies = data.get("feasible_studies", []) + data.get("partial_studies", [])
        for v in data.get("variables") or [v for s in studies for v in s.get("variables", [])]:
            # picsure_search rows say phv_id/study, find_cohort_variables rows variable_id/study_id/study_name
            var = node(v.get("phv_id") or v.get("variable_id"), v.get("variable_name"), "variable")
            edge(var, node(v.get("study") or v.get("study_id"), v.get("study_name"), "study"))
            for c in v.get("matched_concepts", []):
                if isinstance(c, str):  # find_cohort_variables lists the searched term, not a concept ID
                    edge(var, node(c, c, "term"))
                else:
                    edge(var, node(c.get("concept_id"), c.get("concept_name"), category=c.get("concept_type")))

    return {"nodes": list(nodes.values()), "edges": list(edges.values())} if edges else None


async def dug_kg(request, handler):
    result = await handler(request)
    if not isinstance(result, CallToolResult) or result.isError:
        return result
    try:
        data = json.loads(result.content[0].text)
    except (IndexError, AttributeError, ValueError):  # no/non-text block, or text cut at dug's 50k-char cap
        return result
    kg = to_kg(request.name, data) if isinstance(data, dict) else None
    if not kg:
        return result
    structured = {**(result.structuredContent or {}), "kg": {"tool": request.name, "args": request.args, **kg}}
    return result.model_copy(update={"structuredContent": structured})
