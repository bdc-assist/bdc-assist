"""Load test: many users at once against the running services, with the real models.

  search  r-doc-mcp's search_docs over MCP, the way r-assist's agent calls it (a session per call).
          Each search embeds the query on r-doc-mcp's embedding server (its EMBEDDING_URL, e.g. a
          remote Ollama), which is most of the time, then queries Chroma. No LLM: costs nothing.
  dug     the Dug MCP server (mcp_servers.yaml's dug_mcp): search_concepts with find_variables, the
          agent's usual first Dug call, for disease terms. A shared RENCI service: keep it light.
  chat    r-assist's /chat/stream end to end: guardrails, classifier, the agent and its tools,
          follow-ups. Real LLM calls: every request costs tokens.

    uv run python tests/loadtest.py search                          # 1, 10, 50, 100 users
    uv run python tests/loadtest.py chat --users 50 100 --requests 1  # 150 chats: mind the token bill

Start the services first (demo_services.sh). Each level runs that many users at once, each sending
--requests questions back to back (CONFIG_DIR's sample_questions.yaml, in turn), after one warm-up
request. It reports completed and failed requests, throughput, latency p50/p95/max and, for chat, the
time to the first answer token. Latency that grows with users while throughput stays flat means
something serves them one at a time; failures that start at some level mark the capacity limit (an LLM
rate limit shows as failed runs, with the cause in the r-assist log).
"""
import argparse
import asyncio
import json
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path

import httpx
import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from langchain_mcp_adapters.sessions import create_session

sys.path.insert(0, str(Path(__file__).parent.parent))  # runnable from anywhere
from r_assist import prompts
from r_assist.agent import _root, load_mcp_servers


async def search(client, args, question):
    async with streamable_http_client(args.mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("search_docs", {"query": question})
    if res.isError:
        raise RuntimeError(res.content[0].text[:200] if res.content else "search_docs failed")
    return None


# disease and measurement terms, as the agent passes them to Dug (not whole questions)
DUG_TERMS = ["heart attack", "asthma", "obesity", "blood pressure", "sleep apnea", "diabetes", "COPD", "smoking"]


async def dug(client, args, question):
    """Dug's search_concepts with find_variables, the agent's usual first Dug call, over the connection
    in mcp_servers.yaml (the interceptor that shapes it for the agent is not run)."""
    async with create_session(load_mcp_servers()["dug_mcp"]) as session:
        await session.initialize()
        res = await session.call_tool("search_concepts", {"search_term": question, "find_variables": True})
    if res.isError:
        raise RuntimeError(res.content[0].text[:200] if res.content else "search_concepts failed")
    return None


async def chat(client, args, question):
    """-> seconds to the first answer token (None for a canned answer, which streams none)."""
    start, first = time.monotonic(), None
    async with client.stream("POST", f"{args.api_url}/chat/stream",
                             json={"input": question, "chat_history": []}) as res:
        res.raise_for_status()
        async for line in res.aiter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line.removeprefix("data: "))
            if event["type"] == "token" and first is None:
                first = time.monotonic() - start
            elif event["type"] == "error":
                raise RuntimeError("busy: the line was full" if event.get("busy")
                                   else "the run failed (details in the r-assist log)")
            elif event["type"] == "done":
                return first
    raise RuntimeError("the stream ended without a done event")


def pct(values, p):
    """Nearest-rank percentile; nan for no values."""
    s = sorted(values)
    return s[max(0, math.ceil(p * len(s)) - 1)] if s else math.nan


async def run_level(target, client, args, questions, users):
    """users at once, each sending args.requests questions back to back
    -> (wall seconds, latencies, first-token times, Counter of errors)."""
    latencies, firsts, errors = [], [], Counter()

    async def user(u):
        for i in range(args.requests):
            question = questions[(u * args.requests + i) % len(questions)]
            start = time.monotonic()
            try:
                first = await asyncio.wait_for(target(client, args, question), args.timeout)
            except Exception as e:
                e = _root(e)
                errors[f"{type(e).__name__}: {(str(e).splitlines() or [''])[0][:150]}"] += 1
                continue
            latencies.append(time.monotonic() - start)
            if first is not None:
                firsts.append(first)

    start = time.monotonic()
    await asyncio.gather(*(user(u) for u in range(users)))
    return time.monotonic() - start, latencies, firsts, errors


async def run(args):
    target = {"search": search, "dug": dug, "chat": chat}[args.target]
    with open(prompts.DATA_DIR / "sample_questions.yaml", encoding="utf-8") as f:
        questions = DUG_TERMS if args.target == "dug" else [str(q) for q in yaml.safe_load(f)]
    # no connection cap: a pooled client would queue the users itself
    limits = httpx.Limits(max_connections=None, max_keepalive_connections=None)
    async with httpx.AsyncClient(timeout=args.timeout, limits=limits) as client:
        start = time.monotonic()
        await target(client, args, questions[0])  # model loads and first-run imports are not load
        where = {"search": args.mcp_url, "dug": load_mcp_servers()["dug_mcp"]["url"], "chat": args.api_url}
        print(f"{args.target} -> {where[args.target]}: "
              f"warm-up {time.monotonic() - start:.2f}s, {args.requests} requests per user")
        head = f"{'users':>5} {'ok':>5} {'failed':>6} {'req/s':>6} {'p50':>7} {'p95':>7} {'max':>7}"
        print(head + (f" {'1st tok p50':>11} {'p95':>7}" if args.target == "chat" else ""))
        for users in args.users:
            wall, lat, firsts, errors = await run_level(target, client, args, questions, users)
            row = (f"{users:5d} {len(lat):5d} {sum(errors.values()):6d} {len(lat) / wall:6.2f} "
                   f"{pct(lat, .5):6.2f}s {pct(lat, .95):6.2f}s {max(lat, default=math.nan):6.2f}s")
            if args.target == "chat":
                row += f" {pct(firsts, .5):10.2f}s {pct(firsts, .95):6.2f}s"
            print(row)
            for error, n in errors.most_common(3):
                print(f"      {n} x {error}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target", choices=["search", "dug", "chat"])
    parser.add_argument("--users", type=int, nargs="+", default=[1, 10, 50, 100],
                        help="concurrent users, one level each (default: %(default)s)")
    parser.add_argument("--requests", type=int, default=3, help="questions each user sends, back to back (default: 3)")
    parser.add_argument("--mcp-url", default=f"http://127.0.0.1:{os.getenv('MCP_PORT', '8001')}/mcp",
                        help="r-doc-mcp's MCP endpoint (default: %(default)s)")
    parser.add_argument("--api-url", default=f"http://127.0.0.1:{os.getenv('API_PORT', '8010')}",
                        help="r-assist's API (default: %(default)s)")
    parser.add_argument("--timeout", type=float, default=180, help="seconds before one request counts as failed")
    asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
