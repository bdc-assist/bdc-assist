import argparse
import asyncio

import loadtest


def test_run_level_overlaps_users_and_counts_failures():
    async def target(client, args, question):
        await asyncio.sleep(0.2)
        if question == "bad":
            raise RuntimeError("boom")
        return 0.1

    args = argparse.Namespace(requests=2, timeout=5)
    wall, lat, firsts, errors = asyncio.run(loadtest.run_level(target, None, args, ["a", "bad", "c"], 3))
    # 3 users x 2 requests back to back: two rounds of 0.2s, not six
    assert wall < 0.6, wall
    assert len(lat) == 4 and firsts == [0.1] * 4 and errors == {"RuntimeError: boom": 2}, (lat, errors)


def test_a_request_past_the_timeout_counts_as_failed():
    async def hangs(client, args, question):
        await asyncio.sleep(10)

    _, lat, _, errors = asyncio.run(loadtest.run_level(hangs, None, argparse.Namespace(requests=1, timeout=0.1), ["q"], 1))
    assert lat == [] and sum(errors.values()) == 1, errors


def test_pct_is_nearest_rank():
    assert loadtest.pct([3, 1, 2, 4], .5) == 2
    assert loadtest.pct(list(range(1, 21)), .95) == 19
    assert loadtest.pct([], .5) != loadtest.pct([], .5)  # nan
