# Sample MCP responses (BDC example)

What the BDC bot's two MCP servers return to the agent, captured 2026-10-04 by calling them directly
(MCP `tools/call`, no r-assist in between); the keyword search and the unreachable-server errors were
recaptured 2026-10-07. These are the raw tool results the agent reads before it writes an answer; the
API responses built from them are in [sample_responses.md](sample_responses.md).

Shortened to stay readable: lists keep a few of their items and a `// … N more` comment, and long text
and URLs are cut with `…`. The `//` comments are explanations, not part of the JSON.

## The servers

Both are listed in [mcp_servers.yaml](mcp_servers.yaml); the agent gets every tool they offer.

| Server | Tool | Arguments (* required) |
|---|---|---|
| `r_doc_mcp` (r-doc-mcp, the BDC docs) | `search_docs` | `query*`, `k`, `mode`, `doc_type`, `date_from`, `date_to` |
| `dug_mcp` (the Dug knowledge graph) | `search_concepts` | `search_term*`, `node_type`, `find_variables`, `limit` |
| | `get_concept_graph` | `concept_id*`, `expand_depth`, `limit` |
| | `get_concept_connections` | `concept_id*`, `node_type_filter`, `limit` |
| | `expand_concept` | `concept_id*`, `max_hops`, `relationship_types`, `limit` |
| | `find_concept_paths` | `source_id*`, `target_id*`, `max_path_length`, `limit` |
| | `search_variables_by_name` | `search_term*`, `limit` |
| | `find_highly_connected_variables` | `min_connections`, `limit` |
| | `picsure_search` | `phv_ids`, `keyword`, `semantic`, `limit` |
| | `find_cohort_variables` | `concepts*`, `require_all`, `variables_per_concept`, `limit` |
| | `list_graph_schema` | `show_counts` |
| | `trapi_query` | `qgraph*`, `limit` |
| | `cypher_query` | `query*`, `limit` |

## The envelope

Every call returns an MCP `CallToolResult`. The data is JSON inside text blocks:

```jsonc
{
  "content": [
    {"type": "text", "text": "{\"content\": \"Accessing and managing data …\", \"metadata\": {…}, \"score\": 0.68}"}
    // r_doc_mcp: one text block per chunk; dug_mcp: always one block
  ],
  "isError": false                    // true only when the tool itself failed (see Errors)
                                      // no "structuredContent": neither server sends it; for dug_mcp,
                                      // r-assist's interceptors.py adds {"kg": …} there (see the end)
}
```

The examples below show each text block decoded.

## r_doc_mcp: search_docs

### Embedding search (the default)

`{"query": "What is PIC-SURE and what can I do with it?", "k": 3}`: 3 blocks, one per chunk.

```jsonc
// block 1 of 3
{
  "content": "Accessing and managing data across various types and formats can be challenging. The Patient-Centered Information Commons: Standardized Unification of Research Elements (PIC-SURE) is a tool designed to address this challenge …",
                                      // the chunk's text (1,639 characters here)
  "metadata": {
    "doc_type": "docs",               // docs, page, faq, video, fellow, update or event
    "hierarchy": "What is the PIC-SURE API?",  // the chunk's headings, joined with ", "
    "source": "written-documentation/…/what-is-the-pic-sure-api.md",  // path in the source repo
    "page_url": "https://github.com/stagecc/bdc-gitbook/blob/HEAD/written-documentation/…/what-is-the-pic-sure-api.md",
    "contextualized_chunk": "This chunk provides an overview of the PIC-SURE API, detailing its purpose, functionality … Accessing and managing data across various types and formats can be challenging. …"
                                      // a summary written by r-doc-builder, then the whole content again:
                                      // the agent reads each chunk's text twice
  },
  "score": 0.684                      // embedding mode: a distance, lower = closer
}
// … 2 more chunks, scores 0.733 and 0.775
```

r-assist builds the response's `sources` from this metadata: one entry per `page_url`, titled from
`title`, else the first heading in `hierarchy`, with `doc_type` as the type.

### Keyword search

`{"query": "picsure open access authorized access", "mode": "keyword", "k": 3}`: same block shape.
"picsure" matches "PIC-SURE" because keyword mode ignores case and punctuation. Recaptured 2026-10-07:
keyword mode now counts whole words only, so "access" no longer scores inside "accessing" and the
scores dropped (they were 33, 32 and 28).

```jsonc
{
  "content": "**Maintaining and Versioning CWL on External Tool Repositories:** [This tutorial](https://sb-biodatacatalyst.readme.io/docs/maintaining-and-versioning-cwl-on-external-tool-repositories) presents best practices …",
                                      // 2,857 characters: chunk size depends on the source document
  "metadata": {
    "doc_type": "docs",
    "hierarchy": "2021-07-09 BioData Catalyst Ecosystem Release Notes, **Introduction**, **New user support materials and documentation**",
    "source": "written-documentation/release-notes/2021-07-09-biodata-catalyst-ecosystem-release-notes.md",
    "page_url": "https://github.com/stagecc/bdc-gitbook/blob/HEAD/written-documentation/release-notes/2021-07-09-biodata-catalyst-ecosystem-release-notes.md",
    "contextualized_chunk": "…"
  },
  "score": 28.0                       // keyword mode: how often the query's words occur as whole words
                                      // (close misspellings count too), higher = better
}
// … 2 more chunks, scores 24.0 and 12.0
```

### Date-filtered search over events

`{"query": "BDC events", "doc_type": "event", "date_from": "2026-06-01", "k": 3}`. Events, updates
and fellows are searched only when `doc_type` names them; dated chunks carry the date.

```jsonc
{
  "content": "Join us for 20-minute tour introducing BDC-Seven Bridges for new and experienced users. …",
  "metadata": {
    "doc_type": "event",
    "title": "BDC Community Half-Hour: BDC-Seven Bridges: From Getting Started to Advanced Workflows",
                                      // the event chunks have one, the docs chunks above don't
                                      // (r-assist then titles the source from hierarchy)
    "date": "2026-12-16",             // what date_from/date_to compare against
    "date_num": 20261216,             // the same date as a number
    "hierarchy": "",
    "source": "2026/12/community-hours/index.mdx",
    "page_url": "https://github.com/stagecc/bdc-web/blob/HEAD/apps/site/src/content/events/2026/12/community-hours/index.mdx",
    "contextualized_chunk": "…"
  },
  "score": 0.919
}
// … 2 more chunks: 2026-08-26 (score 0.986) and 2026-10-14 (1.259); ranked by relevance, not date
```

## dug_mcp: the knowledge graph

Each Dug tool returns its own JSON shape in a single block. The two below are the calls the agent made
for "studies on heart attack" on 2026-10-04; example 10 in [sample_responses.md](sample_responses.md),
captured later, made the same two calls (its `search_concepts` added `node_type` and `limit`).

### search_concepts

`{"search_term": "heart attack", "find_variables": true}`: the term is expanded to synonyms, then
the study variables linked to any of them are returned (26,778 characters).

```jsonc
{
  "search_term": "heart attack",
  "enrichment": {                     // the synonyms the term expanded to
    "curies": ["MONDO:0005068", "UMLS:C4699470" /* … 14 more */],
    "labels": ["myocardial infarction", "Heart attack (myocardial infarction)" /* … */]
                                      // the agent picks a MONDO ID from here for get_concept_graph
  },
  "total_results": 20,                // variables, capped by limit (default 20)
  "variables": [
    {
      "variable_id": "phv00021036.v6.p12",
      "variable_name": "G3A184",
      "variable_description": "have you ever been told by a doctor you had a heart attack or myocardial infarction?",
      "matched_concepts": [           // which synonyms this variable is linked to, and how
        {"concept_id": "MONDO:0005068", "concept_name": "myocardial infarction",
         "concept_type": "biolink:NamedThing",   // always the generic type, never e.g. Disease
         "predicate": "biolink:related_to"}
        // … 4 more
      ]
    }
    // … 19 more variables
  ]
}
```

### get_concept_graph

`{"concept_id": "MONDO:0005068", "expand_depth": 2, "limit": 50}`: the variables linked to the
concept, each with its study (17,215 characters).

```jsonc
{
  "concept_id": "MONDO:0005068",
  "expand_depth": 2,
  "total_results": 50,                // = limit: more variables exist (at least 145, in 15+ studies)
  "graph": [                          // one row per variable
    {
      "concept": "myocardial infarction",
      "concept_id": "MONDO:0005068",
      "concept_type": "biolink.NamedThing",      // "." here, ":" in search_concepts
      "variable_name": "g3a184",
      "variable_id": "phv00273870.v1.p15",
      "study_name": "Framingham Cohort",
      "study_id": "phs000007.v34.p15",          // includes the version: v34 and v31 are separate studies here
      "related_concepts_count": 14    // the variable's other neighbours, its study included
    }
    // … 49 more rows (9 studies)
  ]
}
```

### What r-assist adds: the kg

For `dug_mcp` calls, `interceptors.py` (named in mcp_servers.yaml) turns rows like these into a
trimmed graph and adds it to the result as `structuredContent`. The text the agent reads is unchanged.

```jsonc
// the get_concept_graph result above, after the interceptor
{
  "content": [{"type": "text", "text": "{\"concept_id\": \"MONDO:0005068\", …}"}],  // unchanged
  "isError": false,
  "structuredContent": {
    "kg": {
      "tool": "get_concept_graph",
      "args": {"concept_id": "MONDO:0005068", "expand_depth": 2, "limit": 50},
      "nodes": [{"id": "MONDO:0005068", "name": "myocardial infarction", "category": "NamedThing"} /* … 59 more */],
      "edges": [{"subject": "phv00273870.v1.p15", "object": "MONDO:0005068"} /* … 99 more */]
    }
  }
}
```

r-assist collects these into the API response's `kg` list. The agent never sees them: the MCP adapter
passes `structuredContent` to LangChain as the tool message's artifact, which isn't sent to the model.

## Errors

Three kinds, and r-assist logs each with the tool name and arguments. The first two come from a
server that answered:

```jsonc
// soft failure: a normal result whose JSON holds "error" (dug_mcp catches its own exceptions)
// cypher_query {"query": "MATCH (c {id: \"MONDO:0005068\"})-[]-(v:`biolink.StudyVariable`) RETURN count(DISTINCT v) AS variables"}
{
  "content": [{"type": "text", "text": "{\n  \"error\": \"Error executing tool 'cypher_query': 'QueryResult' object has no attribute 'execution_time'\"\n}"}],
  "isError": false                    // a dug-mcp bug: cypher_query fails whenever its query returns rows
}
```

```jsonc
// hard failure: isError true. search_docs while the embedding server (Ollama) was down,
// as recorded in the r-assist log
{
  "content": [{"type": "text", "text": "Error executing tool search_docs: Failed to connect to Ollama. Please check that Ollama is downloaded, running and accessible. https://ollama.com/download"}],
  "isError": true                     // r-assist retries once, then the agent gets the error text
}
```

The third is a server that doesn't answer at all, so there is no MCP result: r-assist writes the tool
result itself (captured 2026-10-07).

```jsonc
// unreachable mid-session: dug_mcp went down after r-assist loaded its tools. The call fails twice
// (r-assist retries once), then the agent gets this as the tool result instead of the request failing,
// and r-assist re-checks the servers at once
"Error: search_concepts failed (ConnectError: All connection attempts failed); its server may be down."
```

```jsonc
// unreachable at startup or at a re-check: dug_mcp's tools aren't loaded. Each name in its stand_ins
// (mcp_servers.yaml) is a tool that returns only this, so a question the prompt routes to Dug gets an
// error to follow instead of an answer improvised from the docs
"Error: the dug_mcp service is unavailable right now. Tell the user; do not fill in its part of the answer from other tools."
```

In every case the agent sees the error text. `prompts.yaml` tells it to say the service is unavailable
and not guess. It doesn't always comply: on 2026-10-04, with `search_docs` failing as
above, it still added a general description of BDC to its "I can't access the documentation" answer.
For the unreachable cases, see example 12 in [sample_responses.md](sample_responses.md).
