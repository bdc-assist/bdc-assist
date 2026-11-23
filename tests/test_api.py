"""api.py's chat routes: what reaches the graph."""

from fastapi.testclient import TestClient

from r_assist import api
from r_assist.graph import BotState


class FakeGraph:
    def __init__(self):
        self.inputs = []

    async def ainvoke(self, state):
        self.inputs.append(state)
        return {"answer": "ok"}

    async def astream(self, state, stream_mode=None):
        self.inputs.append(state)
        yield "values", {"answer": "ok"}


def test_client_state_reaches_the_graph(monkeypatch):
    # what the client sends back about itself (the web client: the forms it was offered), for config nodes
    g = FakeGraph()
    monkeypatch.setattr(api, "graph", g)
    monkeypatch.setattr(api, "extra_fields", [], raising=False)
    monkeypatch.setattr(api, "mcp_errors", [], raising=False)
    state = {"offered_forms": [{"id": "get-help", "question": "Who fixes my login loop?"}]}
    c = TestClient(api.app)
    assert c.post("/chat", json={"input": "q", "chat_history": [], "client_state": state}).status_code == 200
    assert '"type": "done"' in c.post("/chat/stream", json={"input": "q", "client_state": state}).text
    assert [i["client_state"] for i in g.inputs] == [state, state]
    c.post("/chat", json={"input": "q"})
    assert g.inputs[-1]["client_state"] == {}  # an older client sends none
    assert "client_state" in BotState.__annotations__  # else the graph drops it before the nodes see it
