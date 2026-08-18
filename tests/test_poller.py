"""Unit tests for feed_subscriber_app/poller.py — cache I/O, diff detection,
webhook/agent dispatch, one iteration of poll_card, and PollerSupervisor's
reconcile (start/cancel per-card tasks).

Async helpers are exercised via asyncio.run() (matches agentic-workspace's
own test_feed_subscriber.py style) rather than pytest-asyncio.

Run: python -m pytest tests/test_poller.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from feed_subscriber_app import poller as fs  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# cache_path / load_cache / save_cache
# ---------------------------------------------------------------------------

class TestCache:
    def test_cache_path_sanitizes_id(self, tmp_path):
        with patch.object(fs, "CACHE_DIR", str(tmp_path)):
            path = fs.cache_path("feed 1/../weird:id")
            assert os.path.dirname(path) == str(tmp_path)
            assert "/" not in os.path.basename(path)[:-5]
            assert ".." not in os.path.basename(path)

    def test_load_cache_missing_file_returns_default(self, tmp_path):
        with patch.object(fs, "CACHE_DIR", str(tmp_path)):
            cache = fs.load_cache("does-not-exist")
        assert cache == {"last_response": None, "last_fetched_at": None, "history": []}

    def test_load_cache_corrupt_json_returns_default(self, tmp_path):
        with patch.object(fs, "CACHE_DIR", str(tmp_path)):
            path = fs.cache_path("bad-card")
            os.makedirs(tmp_path, exist_ok=True)
            with open(path, "w") as f:
                f.write("{not valid json")
            cache = fs.load_cache("bad-card")
        assert cache == {"last_response": None, "last_fetched_at": None, "history": []}

    def test_save_cache_then_load_roundtrip(self, tmp_path):
        with patch.object(fs, "CACHE_DIR", str(tmp_path)):
            data = {"last_response": {"a": 1}, "last_fetched_at": "2026-01-01T00:00:00+00:00", "history": []}
            fs.save_cache("card-1", data)
            loaded = fs.load_cache("card-1")
        assert loaded == data

    def test_save_cache_creates_dir(self, tmp_path):
        nested = tmp_path / "nested" / "cache-dir"
        with patch.object(fs, "CACHE_DIR", str(nested)):
            fs.save_cache("card-2", {"last_response": None, "last_fetched_at": None, "history": []})
        assert nested.is_dir()
        assert (nested / "card-2.json").is_file()

    def test_delete_cache_removes_file(self, tmp_path):
        with patch.object(fs, "CACHE_DIR", str(tmp_path)):
            fs.save_cache("card-3", {"last_response": 1, "last_fetched_at": None, "history": []})
            assert os.path.isfile(fs.cache_path("card-3"))
            fs.delete_cache("card-3")
            assert not os.path.isfile(fs.cache_path("card-3"))

    def test_delete_cache_missing_file_is_a_noop(self, tmp_path):
        with patch.object(fs, "CACHE_DIR", str(tmp_path)):
            fs.delete_cache("never-existed")  # must not raise


# ---------------------------------------------------------------------------
# diff_summary
# ---------------------------------------------------------------------------

class TestDiffSummary:
    def test_new_list_entries_reported(self):
        summary = fs.diff_summary([{"id": 1}], [{"id": 1}, {"id": 2}])
        assert "1 new entry" in summary
        assert '"id": 2' in summary

    def test_multiple_new_entries_counted(self):
        summary = fs.diff_summary([], [{"id": 1}, {"id": 2}, {"id": 3}])
        assert "3 new entry" in summary

    def test_no_new_entries_falls_back_to_content_changed(self):
        summary = fs.diff_summary([{"id": 1}], [{"id": 1}])
        assert summary.startswith("content changed:")

    def test_non_list_payload_uses_content_changed(self):
        summary = fs.diff_summary({"status": "old"}, {"status": "new"})
        assert summary.startswith("content changed:")
        assert '"status": "new"' in summary


# ---------------------------------------------------------------------------
# dispatch_webhook
# ---------------------------------------------------------------------------

class TestDispatchWebhook:
    def test_posts_to_webhook_url_with_bearer_token(self):
        card = {"name": "Feed A", "webhook_url": "https://example.com/hook", "auth_token": "secret123"}
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

        _run(fs.dispatch_webhook(mock_client, card, "hello world"))

        args, kwargs = mock_client.post.call_args
        assert args[0] == "https://example.com/hook"
        assert kwargs["headers"]["Authorization"] == "Bearer secret123"
        assert kwargs["json"] == {"feed": "Feed A", "message": "hello world"}

    def test_no_auth_header_when_token_missing(self):
        card = {"name": "Feed A", "webhook_url": "https://example.com/hook"}
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

        _run(fs.dispatch_webhook(mock_client, card, "msg"))

        _, kwargs = mock_client.post.call_args
        assert "Authorization" not in kwargs["headers"]

    def test_empty_webhook_url_skips_post(self):
        mock_client = MagicMock()
        mock_client.post = AsyncMock()

        _run(fs.dispatch_webhook(mock_client, {"name": "Feed A", "webhook_url": ""}, "msg"))

        mock_client.post.assert_not_called()

    def test_post_exception_does_not_raise(self):
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("boom"))

        _run(fs.dispatch_webhook(mock_client, {"name": "Feed A", "webhook_url": "https://example.com/hook"}, "msg"))


# ---------------------------------------------------------------------------
# dispatch_agent
# ---------------------------------------------------------------------------

class TestDispatchAgent:
    def test_posts_to_agents_platform_run_endpoint(self):
        card = {"name": "Feed A", "agent_slug": "my-agent", "target_slug": "my-target"}
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200, text="ok"))

        _run(fs.dispatch_agent(mock_client, card, "change detected",
                                agents_platform_base="http://ap.example.com", agents_platform_token=None))

        args, kwargs = mock_client.post.call_args
        assert args[0] == "http://ap.example.com/api/agents/my-agent/run"
        assert kwargs["json"] == {"input": "change detected", "target_slug": "my-target"}
        assert "Authorization" not in kwargs["headers"]

    def test_bearer_token_sent_when_configured(self):
        card = {"name": "Feed A", "agent_slug": "my-agent"}
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200, text="ok"))

        _run(fs.dispatch_agent(mock_client, card, "msg",
                                agents_platform_base="http://ap.example.com", agents_platform_token="tok"))

        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer tok"

    def test_default_target_slug_when_missing(self):
        card = {"name": "Feed A", "agent_slug": "my-agent"}
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200, text="ok"))

        _run(fs.dispatch_agent(mock_client, card, "msg",
                                agents_platform_base="http://ap.example.com", agents_platform_token=None))

        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["target_slug"] == "w-feed-subscriber"

    def test_empty_agent_slug_skips_post(self):
        mock_client = MagicMock()
        mock_client.post = AsyncMock()

        _run(fs.dispatch_agent(mock_client, {"name": "Feed A", "agent_slug": ""}, "msg",
                                agents_platform_base="http://ap.example.com", agents_platform_token=None))

        mock_client.post.assert_not_called()

    def test_post_exception_does_not_raise(self):
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("boom"))

        _run(fs.dispatch_agent(mock_client, {"name": "Feed A", "agent_slug": "my-agent"}, "msg",
                                agents_platform_base="http://ap.example.com", agents_platform_token=None))


# ---------------------------------------------------------------------------
# poll_card — one iteration: change detection end-to-end against a mocked
# httpx client, verifying cache is updated and the right dispatch fires.
# ---------------------------------------------------------------------------

class TestPollCardIteration:
    @pytest.fixture(autouse=True)
    def _cache_dir(self, tmp_path):
        with patch.object(fs, "CACHE_DIR", str(tmp_path)):
            yield tmp_path

    def _run_one_iteration(self, card, get_response, call_type="webhook"):
        card = {**card, "call_type": call_type, "id": card.get("id", "card-1")}

        async def fake_get(url):
            return get_response

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=fake_get)
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200, text="ok"))

        class _StopLoop(Exception):
            pass

        async def fake_sleep(_interval):
            raise _StopLoop()

        async def fake_aenter(self):
            return mock_client

        async def fake_aexit(self, *a):
            return False

        def get_card(_card_id):
            return card

        def get_agents_platform():
            return "http://ap.example.com", None

        with patch.object(fs, "asyncio", wraps=asyncio) as mock_asyncio, \
             patch.object(httpx.AsyncClient, "__aenter__", fake_aenter), \
             patch.object(httpx.AsyncClient, "__aexit__", fake_aexit):
            mock_asyncio.sleep = fake_sleep
            with pytest.raises(_StopLoop):
                _run(fs.poll_card(card["id"], get_card, get_agents_platform))

        return mock_client

    def test_first_poll_no_previous_cache_no_dispatch(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=[{"id": 1}])

        card = {"id": "card-1", "name": "Feed A", "source_url": "https://example.com/f.json",
                "poll_interval_seconds": 1, "webhook_url": "https://hook.example.com"}
        client = self._run_one_iteration(card, resp)

        client.post.assert_not_called()
        cache = fs.load_cache("card-1")
        assert cache["last_response"] == [{"id": 1}]

    def test_change_detected_dispatches_webhook(self):
        fs.save_cache("card-1", {"last_response": [{"id": 1}], "last_fetched_at": None, "history": []})

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=[{"id": 1}, {"id": 2}])

        card = {"id": "card-1", "name": "Feed A", "source_url": "https://example.com/f.json",
                "poll_interval_seconds": 1, "webhook_url": "https://hook.example.com"}
        client = self._run_one_iteration(card, resp, call_type="webhook")

        args, kwargs = client.post.call_args
        assert args[0] == "https://hook.example.com"
        assert "1 new entry" in kwargs["json"]["message"]

        cache = fs.load_cache("card-1")
        assert cache["last_response"] == [{"id": 1}, {"id": 2}]
        assert len(cache["history"]) == 1

    def test_change_detected_dispatches_agent_call(self):
        fs.save_cache("card-1", {"last_response": {"v": 1}, "last_fetched_at": None, "history": []})

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"v": 2})

        card = {"id": "card-1", "name": "Feed A", "source_url": "https://example.com/f.json",
                "poll_interval_seconds": 1, "agent_slug": "my-agent", "target_slug": "w-feed-subscriber"}
        client = self._run_one_iteration(card, resp, call_type="agent")

        args, _ = client.post.call_args
        assert args[0] == "http://ap.example.com/api/agents/my-agent/run"

    def test_no_change_no_dispatch(self):
        fs.save_cache("card-1", {"last_response": [{"id": 1}], "last_fetched_at": None, "history": []})

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=[{"id": 1}])

        card = {"id": "card-1", "name": "Feed A", "source_url": "https://example.com/f.json",
                "poll_interval_seconds": 1, "webhook_url": "https://hook.example.com"}
        client = self._run_one_iteration(card, resp)

        client.post.assert_not_called()

    def test_card_removed_from_config_stops_loop(self):
        _run(fs.poll_card("card-1", lambda _id: None, lambda: ("http://ap.example.com", None)))


# ---------------------------------------------------------------------------
# PollerSupervisor.reconcile — one task per card, cancelled when removed
# ---------------------------------------------------------------------------

class TestPollerSupervisorReconcile:
    def test_starts_one_task_per_configured_card(self):
        created = []

        def fake_create_task(coro):
            created.append(coro)
            coro.close()  # avoid "coroutine was never awaited" warnings
            return MagicMock()

        supervisor = fs.PollerSupervisor(
            get_cards=lambda: [{"id": "a"}, {"id": "b"}],
            get_agents_platform=lambda: ("http://ap.example.com", None),
        )

        with patch.object(fs.asyncio, "create_task", side_effect=fake_create_task):
            _run(supervisor.reconcile())

        assert len(created) == 2
        assert set(supervisor.task_ids()) == {"a", "b"}

    def test_second_reconcile_does_not_recreate_existing_tasks(self):
        supervisor = fs.PollerSupervisor(
            get_cards=lambda: [{"id": "a"}],
            get_agents_platform=lambda: ("http://ap.example.com", None),
        )

        def fake_create_task(coro):
            coro.close()
            return MagicMock()

        with patch.object(fs.asyncio, "create_task", side_effect=fake_create_task) as mock_create:
            _run(supervisor.reconcile())
            _run(supervisor.reconcile())

        assert mock_create.call_count == 1

    def test_cancels_task_for_card_removed_from_config(self):
        cards = [{"id": "a"}, {"id": "b"}]
        supervisor = fs.PollerSupervisor(
            get_cards=lambda: cards,
            get_agents_platform=lambda: ("http://ap.example.com", None),
        )

        def fake_create_task(coro):
            coro.close()
            return MagicMock()

        with patch.object(fs.asyncio, "create_task", side_effect=fake_create_task):
            _run(supervisor.reconcile())
            task_b = supervisor._tasks["b"]
            cards.pop()  # remove card "b"
            _run(supervisor.reconcile())

        task_b.cancel.assert_called_once()
        assert set(supervisor.task_ids()) == {"a"}

    def test_cancel_all_cancels_every_task_and_clears(self):
        supervisor = fs.PollerSupervisor(
            get_cards=lambda: [{"id": "a"}, {"id": "b"}],
            get_agents_platform=lambda: ("http://ap.example.com", None),
        )

        def fake_create_task(coro):
            coro.close()
            return MagicMock()

        with patch.object(fs.asyncio, "create_task", side_effect=fake_create_task):
            _run(supervisor.reconcile())

        tasks = list(supervisor._tasks.values())
        supervisor.cancel_all()

        for t in tasks:
            t.cancel.assert_called_once()
        assert supervisor.task_ids() == []
