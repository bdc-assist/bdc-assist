# r-assist workflow

The mermaid source of the workflow pictures. Written by `docs/draw_workflow.py`: edit the code
and rerun it, not this file.

## The BDC config (workflow_bdc.png)

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	input_guardrail(input_guardrail)
	contextualize(contextualize)
	classify(classify)
	main_agent(main_agent)
	output_guardrail(output_guardrail)
	suggest_followups(suggest_followups)
	related_work(related_work)
	append_disclaimer(append_disclaimer)
	gate(gate)
	finish(finish)
	__end__([<p>__end__</p>]):::last
	__start__ --> contextualize;
	classify --> gate;
	contextualize --> classify;
	contextualize --> input_guardrail;
	finish -. &nbsp;done or blocked&nbsp; .-> __end__;
	finish -. &nbsp;disclaimer&nbsp; .-> append_disclaimer;
	gate -. &nbsp;blocked or canned&nbsp; .-> __end__;
	gate -. &nbsp;regular&nbsp; .-> main_agent;
	input_guardrail --> gate;
	main_agent --> output_guardrail;
	main_agent --> related_work;
	main_agent --> suggest_followups;
	output_guardrail --> finish;
	related_work --> finish;
	suggest_followups --> finish;
	append_disclaimer --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
	classDef agent fill:#fecaca,stroke:#f87171
	class main_agent agent
```

## The BDC config, inside main_agent too (workflow_bdc_detailed.png)

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	input_guardrail(input_guardrail)
	contextualize(contextualize)
	classify(classify)
	output_guardrail(output_guardrail)
	suggest_followups(suggest_followups)
	related_work(related_work)
	append_disclaimer(append_disclaimer)
	gate(gate)
	finish(finish)
	__end__([<p>__end__</p>]):::last
	__start__ --> contextualize;
	classify --> gate;
	contextualize --> classify;
	contextualize --> input_guardrail;
	finish -. &nbsp;done or blocked&nbsp; .-> __end__;
	finish -. &nbsp;disclaimer&nbsp; .-> append_disclaimer;
	gate -. &nbsp;blocked or canned&nbsp; .-> __end__;
	gate -. &nbsp;regular&nbsp; .-> main_agent;
	input_guardrail --> gate;
	main_agent --> output_guardrail;
	main_agent --> related_work;
	main_agent --> suggest_followups;
	output_guardrail --> finish;
	related_work --> finish;
	suggest_followups --> finish;
	append_disclaimer --> __end__;
	subgraph main_agent
	main_agent\3atools(tools)
	main_agent\3amodel(model)
	main_agent\3ar_doc_mcp(r_doc_mcp)
	main_agent\3adug_mcp(dug_mcp + dug_kg)
	main_agent\3aartie_mcp(artie_mcp)
	main_agent\3amodel -.-> main_agent\3atools;
	main_agent\3atools -.-> main_agent\3amodel;
	main_agent\3atools -.-> main_agent\3ar_doc_mcp;
	main_agent\3atools -.-> main_agent\3adug_mcp;
	main_agent\3atools -.-> main_agent\3aartie_mcp;
	end
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
	classDef agent fill:#fecaca,stroke:#f87171
	class main_agent agent
	classDef own fill:#bbf7d0,stroke:#4ade80
	class related_work,main_agent\3ar_doc_mcp,main_agent\3adug_mcp,main_agent\3aartie_mcp own
```

## General purpose, every slot with a placeholder (workflow_general.png)

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	input_guardrail(input_guardrail)
	contextualize(contextualize)
	classify(classify)
	before_agent_nodes(before_agent_nodes)
	main_agent(main_agent)
	output_guardrail(output_guardrail)
	suggest_followups(suggest_followups)
	after_agent_nodes(after_agent_nodes)
	after_finish_nodes(after_finish_nodes)
	append_disclaimer(append_disclaimer)
	gate(gate)
	finish(finish)
	__end__([<p>__end__</p>]):::last
	__start__ --> contextualize;
	after_agent_nodes --> finish;
	after_finish_nodes -. &nbsp;done&nbsp; .-> __end__;
	after_finish_nodes -. &nbsp;disclaimer&nbsp; .-> append_disclaimer;
	before_agent_nodes -. &nbsp;answered&nbsp; .-> __end__;
	before_agent_nodes -. &nbsp;go on&nbsp; .-> main_agent;
	classify --> gate;
	contextualize --> classify;
	contextualize --> input_guardrail;
	finish -. &nbsp;blocked&nbsp; .-> __end__;
	finish -. &nbsp;passed&nbsp; .-> after_finish_nodes;
	gate -. &nbsp;blocked or canned&nbsp; .-> __end__;
	gate -. &nbsp;regular&nbsp; .-> before_agent_nodes;
	input_guardrail --> gate;
	main_agent --> after_agent_nodes;
	main_agent --> output_guardrail;
	main_agent --> suggest_followups;
	output_guardrail --> finish;
	suggest_followups --> finish;
	append_disclaimer --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
	classDef agent fill:#fecaca,stroke:#f87171
	class main_agent agent
	classDef own fill:#bbf7d0,stroke:#4ade80
	class before_agent_nodes,after_agent_nodes,after_finish_nodes own
```

## General purpose, every slot with a placeholder, inside main_agent too (workflow_general_detailed.png)

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	input_guardrail(input_guardrail)
	contextualize(contextualize)
	classify(classify)
	before_agent_nodes(before_agent_nodes)
	output_guardrail(output_guardrail)
	suggest_followups(suggest_followups)
	after_agent_nodes(after_agent_nodes)
	after_finish_nodes(after_finish_nodes)
	append_disclaimer(append_disclaimer)
	gate(gate)
	finish(finish)
	__end__([<p>__end__</p>]):::last
	__start__ --> contextualize;
	after_agent_nodes --> finish;
	after_finish_nodes -. &nbsp;done&nbsp; .-> __end__;
	after_finish_nodes -. &nbsp;disclaimer&nbsp; .-> append_disclaimer;
	before_agent_nodes -. &nbsp;answered&nbsp; .-> __end__;
	before_agent_nodes -. &nbsp;go on&nbsp; .-> main_agent;
	classify --> gate;
	contextualize --> classify;
	contextualize --> input_guardrail;
	finish -. &nbsp;blocked&nbsp; .-> __end__;
	finish -. &nbsp;passed&nbsp; .-> after_finish_nodes;
	gate -. &nbsp;blocked or canned&nbsp; .-> __end__;
	gate -. &nbsp;regular&nbsp; .-> before_agent_nodes;
	input_guardrail --> gate;
	main_agent --> after_agent_nodes;
	main_agent --> output_guardrail;
	main_agent --> suggest_followups;
	output_guardrail --> finish;
	suggest_followups --> finish;
	append_disclaimer --> __end__;
	subgraph main_agent
	main_agent\3atools(tools)
	main_agent\3amodel(model)
	main_agent\3ar_doc_mcp(r_doc_mcp)
	main_agent\3amore_mcp_servers(more_mcp_servers + their_interceptors)
	main_agent\3amodel -.-> main_agent\3atools;
	main_agent\3atools -.-> main_agent\3amodel;
	main_agent\3atools -.-> main_agent\3ar_doc_mcp;
	main_agent\3atools -.-> main_agent\3amore_mcp_servers;
	end
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
	classDef agent fill:#fecaca,stroke:#f87171
	class main_agent agent
	classDef own fill:#bbf7d0,stroke:#4ade80
	class before_agent_nodes,after_agent_nodes,after_finish_nodes,main_agent\3ar_doc_mcp,main_agent\3amore_mcp_servers own
```
