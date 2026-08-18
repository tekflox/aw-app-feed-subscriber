"""TestClient coverage of routes.py's sub-app: card CRUD, the agent picker
proxy, and the MCP-over-HTTP endpoint.

``_save_cards`` is monkeypatched everywhere — it does a real HTTP loopback
call to core's own ``POST /api/apps/feed-subscriber/config`` (see routes.py's
module docstring for why persistence goes through that route instead of
mutating ``ctx.config`` in place), which does not exist in a bare pytest
run. Patching it here also lets each test assert exactly what final card
list a request would have persisted, without a running aw-workspace.

Run: python -m pytest tests/test_routes.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from feed_subscriber_app import routes  # noqa: E402


class FakeSecrets:
    def __init__(self):
        self._store: dict[str, str] = {}

    def read(self, key: str) -> str | None:
        return self._store.get(key)

    def write(self, key: str, value: str) -> None:
        self._store[key] = value

    def delete(self, key: str) -> bool:
        return self._store.pop(key, None) is not None


class FakeCtx:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.secrets = FakeSecrets()


@pytest.fixture
def ctx():
    return FakeCtx({"cards": []})


@pytest.fixture
def client(ctx):
    app = routes.build_routes(ctx)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _patch_save_cards():
    with patch.object(routes, "_save_cards", new=AsyncMock()) as mock:
        yield mock


class TestCardCrud:
    def test_list_cards_empty(self, client):
        resp = client.get("/cards")
        assert resp.status_code == 200
        assert resp.json() == {"cards": []}

    def test_create_card_requires_id(self, client):
        resp = client.post("/cards", json={"name": "no id"})
        assert resp.status_code == 400

    def test_create_card_persists_redacted_and_secrets_auth_token(self, client, ctx, _patch_save_cards):
        resp = client.post("/cards", json={
            "id": "card-1", "name": "Feed A", "source_url": "https://example.com/f.json",
            "call_type": "webhook", "webhook_url": "https://hook.example.com",
            "auth_token": "supersecret",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_auth_token"] is True
        assert "auth_token" not in body["card"]

        _patch_save_cards.assert_awaited_once()
        (persisted_cards,), _ = _patch_save_cards.call_args
        assert len(persisted_cards) == 1
        assert "auth_token" not in persisted_cards[0]
        assert ctx.secrets.read("card:card-1:auth_token") == "supersecret"

    def test_create_card_without_auth_token_stores_nothing_in_secrets(self, client, ctx):
        client.post("/cards", json={"id": "card-1", "name": "Feed A"})
        assert ctx.secrets.read("card:card-1:auth_token") is None

    def test_create_duplicate_id_conflicts(self, client, ctx):
        ctx.config["cards"] = [{"id": "card-1", "name": "existing"}]
        resp = client.post("/cards", json={"id": "card-1", "name": "dup"})
        assert resp.status_code == 409

    def test_list_cards_reports_has_auth_token(self, client, ctx):
        ctx.config["cards"] = [{"id": "card-1", "name": "Feed A"}]
        ctx.secrets.write("card:card-1:auth_token", "tok")
        resp = client.get("/cards")
        assert resp.json()["cards"][0]["has_auth_token"] is True

    def test_update_card_not_found(self, client):
        resp = client.put("/cards/nope", json={"name": "x"})
        assert resp.status_code == 404

    def test_update_card_replaces_fields_and_rotates_auth_token(self, client, ctx, _patch_save_cards):
        ctx.config["cards"] = [{"id": "card-1", "name": "Old name", "source_url": "https://old"}]
        ctx.secrets.write("card:card-1:auth_token", "old-token")

        resp = client.put("/cards/card-1", json={"name": "New name", "auth_token": "new-token"})
        assert resp.status_code == 200
        assert resp.json()["card"]["name"] == "New name"
        assert ctx.secrets.read("card:card-1:auth_token") == "new-token"

        (persisted_cards,), _ = _patch_save_cards.call_args
        assert persisted_cards[0]["name"] == "New name"
        assert "auth_token" not in persisted_cards[0]

    def test_update_card_blank_auth_token_clears_secret(self, client, ctx):
        ctx.config["cards"] = [{"id": "card-1", "name": "Feed A"}]
        ctx.secrets.write("card:card-1:auth_token", "old-token")

        client.put("/cards/card-1", json={"name": "Feed A", "auth_token": ""})

        assert ctx.secrets.read("card:card-1:auth_token") is None

    def test_delete_card_removes_secret_and_cache(self, client, ctx, _patch_save_cards):
        ctx.config["cards"] = [{"id": "card-1", "name": "Feed A"}]
        ctx.secrets.write("card:card-1:auth_token", "tok")

        with patch.object(routes.poller_mod, "delete_cache") as mock_delete_cache:
            resp = client.delete("/cards/card-1")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": "card-1"}
        assert ctx.secrets.read("card:card-1:auth_token") is None
        mock_delete_cache.assert_called_once_with("card-1")
        (persisted_cards,), _ = _patch_save_cards.call_args
        assert persisted_cards == []


class TestAgentsProxy:
    def test_list_agents_returns_empty_on_failure(self, client):
        resp = client.get("/agents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["agents"] == []
        assert "error" in body

    def test_list_agents_success(self, client, ctx):
        ctx.config["agents_platform_base"] = "http://agents.example.com"

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"agents": [{"slug": "a", "name": "Agent A"}]}

        async def fake_get(self, url, headers=None):
            assert url == "http://agents.example.com/api/agents"
            return FakeResp()

        with patch("httpx.AsyncClient.get", fake_get):
            resp = client.get("/agents")
        assert resp.json()["agents"] == [{"slug": "a", "name": "Agent A"}]


class TestMcpEndpoint:
    def test_tools_list_exposes_list_feed_entries(self, client):
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()["result"]["tools"]]
        assert names == ["list_feed_entries"]

    def test_notification_returns_202(self, client):
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert resp.status_code == 202

    def test_get_mcp_is_405(self, client):
        resp = client.get("/mcp")
        assert resp.status_code == 405
