"""Tool-call interceptors for the BDC bot. mcp_servers.yaml names them per server
(`interceptors: [dug_kg]`); r-assist runs each on that server's tool calls only, as a
langchain-mcp-adapters tool interceptor: async (request, handler) -> result.

dug_kg turns a graph-shaped dug-mcp result into a small knowledge graph, trimmed to what
a graph view draws, and attaches it as the result's structuredContent["kg"]; the studies
dug-mcp cites (its "_sources") go along as structuredContent["sources"]["dug"]. The agent's
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


def label(tool: str, args: dict, kg: dict) -> str:
    """The graph in the user's terms, for a UI to show instead of a tool name: "asthma
    concept graph", "asthma + copd cohort variables". A concept is named from the graph
    when it can be; get_concept_connections doesn't name the concept it was asked about,
    so that one shows its id (a client merging several calls may know the name)."""
    names = {n["id"]: n.get("name", n["id"]) for n in kg.get("nodes", [])}
    concept = names.get(args.get("concept_id"), args.get("concept_id") or "")

    def words(v):
        return " + ".join(x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str) and x)

    text = {
        "get_concept_graph": f"{concept} concept graph",
        "get_concept_connections": f"{concept} related concepts",
        "find_cohort_variables": f"{words(args.get('concepts'))} cohort variables",
        "search_concepts": f"{words(args.get('search_term'))} concept search",
        "picsure_search": f"{words(next((v for v in args.values() if isinstance(v, str)), ''))} PIC-SURE variables",
    }.get(tool, "")
    return text if text.split(" ", 1)[0] else tool.replace("_", " ")  # nothing to name it by: the tool


def seeds(tool: str, data: dict, kg: dict) -> list[str]:
    """The nodes a call asked about, for a view to centre on, as dug-mcp echoes them in its
    result: the concept of get_concept_graph / get_concept_connections, the search words of
    find_cohort_variables (verbatim, as their term nodes are). Searches have none. Only ids
    that are nodes: a search word nothing matched isn't one."""
    asked = {"get_concept_graph": [data.get("concept_id")],
             "get_concept_connections": [data.get("concept_id")],
             "find_cohort_variables": data.get("concepts_searched") or []}.get(tool, [])
    ids = {n["id"] for n in kg["nodes"]}
    return [a for a in asked if a in ids]


def entry(tool: str, args: dict, data: dict, kg: dict) -> dict:
    """The kg as attached: which call made it, its label, its seeds (when it has any), its graph."""
    asked = seeds(tool, data, kg)
    return {"tool": tool, "args": args, "label": label(tool, args or {}, kg), **({"seeds": asked} if asked else {}), **kg}


async def dug_kg(request, handler):
    result = await handler(request)
    if not isinstance(result, CallToolResult) or result.isError:
        return result
    try:
        data = json.loads(result.content[0].text)
    except (IndexError, AttributeError, ValueError):  # no/non-text block, or text cut at dug's 50k-char cap
        return result
    if not isinstance(data, dict):
        return result
    kg = to_kg(request.name, data)
    cited = [s for s in data.get("_sources") or [] if isinstance(s, dict) and s.get("link")]
    if not kg and not cited:
        return result
    structured = dict(result.structuredContent or {})
    if kg:
        structured["kg"] = entry(request.name, request.args, data, kg)
    if cited:  # {title, link, type: "dbgap-study"}, one per study; r-assist lists them under sources
        structured["sources"] = {"dug": [{"title": s.get("title"), "link": s["link"], "type": s.get("type", "")}
                                         for s in cited]}
    return result.model_copy(update={"structuredContent": structured})
