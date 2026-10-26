import asyncio
import json

import httpx

from r_assist import api
from r_assist.api import Admission


async def _take(adm, places):
    """wait_turn to the end, recording each place in line it reports."""
    async for place in adm.wait_turn():
        places.append(place)


def test_line_is_first_come_first_served_and_hands_slots_on():
    async def scenario():
        adm, places = Admission(running=2, waiting=2), {n: [] for n in range(4)}
        for n in (0, 1):
            await _take(adm, places[n])  # free slots: no waiting
        waiters = [asyncio.create_task(_take(adm, places[n])) for n in (2, 3)]
        await asyncio.sleep(0)
        assert adm.busy == 2 and len(adm.line) == 2 and adm.full()
        assert places == {0: [], 1: [], 2: [1], 3: [2]}

        adm.release(held=10.0)  # chat 0 done: its slot goes straight to chat 2
        await waiters[0]
        await asyncio.sleep(1.1)  # chat 3's place is re-read about every second
        assert adm.busy == 2 and not adm.full() and places[3] == [2, 1]
        assert adm.eta(1) == 5.0, "one place ahead, 2 slots, 10 s per chat"

        adm.release(held=10.0)
        await waiters[1]
        adm.release()
        adm.release()
        assert adm.busy == 0 and not adm.line

    asyncio.run(scenario())


def test_a_waiter_that_gives_up_leaves_the_line():
    async def scenario():
        adm, places = Admission(running=1, waiting=5), []
        await _take(adm, [])
        quitter = asyncio.create_task(_take(adm, []))
        stayer = asyncio.create_task(_take(adm, places))
        await asyncio.sleep(0)
        quitter.cancel()  # the client disconnected while waiting
        await asyncio.gather(quitter, return_exceptions=True)
        assert len(adm.line) == 1
        adm.release()
        await stayer
        assert places[0] == 2 and adm.busy == 1 and not adm.line, "the slot went to the one still waiting"

    asyncio.run(scenario())


def test_no_cap_by_default():
    async def scenario():
        adm = Admission(running=0, waiting=0)
        for _ in range(100):
            await _take(adm, [])
        assert not adm.full() and adm.busy == 0

    asyncio.run(scenario())


class SlowGraph:
    async def astream(self, payload, stream_mode=None):
        await asyncio.sleep(0.5)
        yield "values", {"answer": f"answer to {payload['input']}"}


def test_over_capacity_chats_wait_their_turn_then_get_503(monkeypatch):
    """One slot, one place in line: the first chat runs, the second is told it is next in line
    and then answered, the third is refused at once with a 503 and a Retry-After."""
    monkeypatch.setattr(api, "graph", SlowGraph())
    monkeypatch.setattr(api, "admission", Admission(running=1, waiting=1))

    async def user(client, n):
        res = await client.post("/chat/stream", json={"input": f"q{n}", "chat_history": []})
        events = [json.loads(line.removeprefix("data: ")) for line in res.text.split("\n\n") if line.startswith("data: ")]
        return res.status_code, res.headers.get("retry-after"), events

    async def three_users():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://t") as client:
            first = asyncio.create_task(user(client, 0))
            await asyncio.sleep(0.1)
            second = asyncio.create_task(user(client, 1))
            await asyncio.sleep(0.1)
            third = await user(client, 2)
            return await first, await second, third

    (s1, _, e1), (s2, _, e2), (s3, retry, _) = asyncio.run(three_users())
    assert s1 == 200 and e1[-1]["answer"] == "answer to q0" and all(e["type"] != "status" for e in e1)
    assert s2 == 200 and e2[0] == {"type": "status", "text": "Busy: you're number 1 in line"}, e2
    assert e2[-1]["answer"] == "answer to q1"
    assert s3 == 503 and retry, (s3, retry)
    assert api.admission.busy == 0 and not api.admission.line, "every slot handed back"


def test_a_burst_cannot_overfill_the_line(monkeypatch):
    """Requests arriving together all pass the endpoint's check before any has joined the line (seen in
    the load test: 100 users at once, none refused): the limit must hold where they join."""
    monkeypatch.setattr(api, "graph", SlowGraph())
    monkeypatch.setattr(api, "admission", Admission(running=1, waiting=1))

    async def burst():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://t") as client:
            return await asyncio.gather(*(client.post("/chat/stream", json={"input": f"q{n}", "chat_history": []})
                                          for n in range(5)))

    answered = refused = 0
    for res in asyncio.run(burst()):
        if res.status_code == 503 or '"busy": true' in res.text:
            refused += 1
        elif '"type": "done"' in res.text:
            answered += 1
    assert (answered, refused) == (2, 3), (answered, refused)
    assert api.admission.busy == 0 and not api.admission.line
