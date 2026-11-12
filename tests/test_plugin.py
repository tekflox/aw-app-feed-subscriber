"""Regression coverage for the auth_token round-trip plugin.py wires up for
the poller: routes.py strips a card's auth_token into ctx.secrets on every
write, and plugin.py's cards_with_secrets() must merge it back in before the
poller dispatches — otherwise every Webhook Call goes out with no
Authorization header (the exact bug this test guards against).

Goes end-to-end: create_card (via the real routes.py TestClient) -> read the
cards back the way the poller does (cards_with_secrets) -> poll_card ->
assert the real HTTP request carries the Bearer token.

Run: python -m pytest tests/test_plugin.py -q
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from feed_subscriber_app import plugin  # noqa: E402
from feed_subscriber_app import poller as poller_mod  # noqa: E402
from feed_subscriber_app import routes  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


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
def _patch_save_cards(ctx):
    async def fake_save(cards):
        # mirrors the real self-loopback save: the next ctx.config read
        # reflects what was just persisted.
        ctx.config["cards"] = cards

    with patch.object(routes, "_save_cards", new=AsyncMock(side_effect=fake_save)):
        yield


class TestCardsWithSecrets:
    def test_merges_token_back_onto_matching_card(self):
        secrets = FakeSecrets()
        secrets.write("card:card-1:auth_token", "supersecret")

        merged = plugin.cards_with_secrets([{"id": "card-1", "name": "Feed A"}], secrets)

        assert merged[0]["auth_token"] == "supersecret"

    def test_card_without_a_stored_token_is_left_alone(self):
        secrets = FakeSecrets()

        merged = plugin.cards_with_secrets([{"id": "card-1", "name": "Feed A"}], secrets)

        assert "auth_token" not in merged[0]

    def test_does_not_mutate_the_input_card(self):
        secrets = FakeSecrets()
        secrets.write("card:card-1:auth_token", "supersecret")
        card = {"id": "card-1", "name": "Feed A"}

        plugin.cards_with_secrets([card], secrets)

        assert "auth_token" not in card


class TestAuthTokenReachesThePoller:
    def test_create_card_to_poll_card_sends_authorization_header(self, client, ctx, tmp_path):
        resp = client.post("/cards", json={
            "id": "card-1", "name": "Feed A", "source_url": "https://example.com/f.json",
            "call_type": "webhook", "webhook_url": "https://hook.example.com",
            "poll_interval_seconds": 1, "auth_token": "supersecret",
        })
        assert resp.status_code == 200

        # the persisted card (what routes.py wrote to ctx.config) never
        # carries the token in plain text.
        assert "auth_token" not in ctx.config["cards"][0]

        def get_card(card_id):
            cards = plugin.cards_with_secrets(ctx.config["cards"], ctx.secrets)
            return next((c for c in cards if c.get("id") == card_id), None)

        def get_agents_platform():
            return "http://ap.example.com", None

        get_response = MagicMock()
        get_response.raise_for_status = MagicMock()
        get_response.json = MagicMock(return_value={"v": 2})

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=get_response)
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

        class _StopLoop(Exception):
            pass

        async def fake_sleep(_interval):
            raise _StopLoop()

        async def fake_aenter(self):
            return mock_client

        async def fake_aexit(self, *a):
            return False

        with patch.object(poller_mod, "CACHE_DIR", str(tmp_path)), \
             patch.object(poller_mod, "asyncio", wraps=asyncio) as mock_asyncio, \
             patch.object(httpx.AsyncClient, "__aenter__", fake_aenter), \
             patch.object(httpx.AsyncClient, "__aexit__", fake_aexit):
            # seed a different previous response so the poll detects a
            # change and actually dispatches on its first and only
            # iteration.
            poller_mod.save_cache("card-1", {"last_response": {"v": 1}, "last_fetched_at": None, "history": []})
            mock_asyncio.sleep = fake_sleep
            with pytest.raises(_StopLoop):
                _run(poller_mod.poll_card("card-1", get_card, get_agents_platform))

        args, kwargs = mock_client.post.call_args
        assert args[0] == "https://hook.example.com"
        assert kwargs["headers"]["Authorization"] == "Bearer supersecret"
