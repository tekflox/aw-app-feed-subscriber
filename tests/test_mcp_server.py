"""Unit tests for feed_subscriber_app/mcp/http_handler.py — list_feed_entries
tool + JSON-RPC dispatch, against a real cache dir under tmp_path (no
network, no live poller).

Run: python -m pytest tests/test_mcp_server.py -q
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from feed_subscriber_app import poller as poller_mod  # noqa: E402
from feed_subscriber_app.mcp import http_handler as mcp  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path):
    with patch.object(poller_mod, "CACHE_DIR", str(tmp_path)):
        yield tmp_path


def _write_cache(feed_id: str, cache: dict):
    poller_mod.save_cache(feed_id, cache)


class TestListFeedEntries:
    def test_missing_feed_id_is_error(self):
        resp = _run(mcp.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "list_feed_entries", "arguments": {}},
        }))
        assert resp["result"]["isError"] is True
        assert "feed_id is required" in resp["result"]["content"][0]["text"]

    def test_no_cache_yet_is_clear_error(self):
        resp = _run(mcp.handle_request({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "list_feed_entries", "arguments": {"feed_id": "never-polled"}},
        }))
        assert resp["result"]["isError"] is True
        assert "No cache found for feed_id 'never-polled'" in resp["result"]["content"][0]["text"]

    def test_corrupt_cache_is_clear_error(self, tmp_path):
        path = poller_mod.cache_path("bad-feed")
        with open(path, "w") as f:
            f.write("{not json")

        resp = _run(mcp.handle_request({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "list_feed_entries", "arguments": {"feed_id": "bad-feed"}},
        }))
        assert resp["result"]["isError"] is True
        assert "corrupt" in resp["result"]["content"][0]["text"]

    def test_returns_cached_state(self):
        _write_cache("feed-1", {
            "last_response": [{"id": 1}, {"id": 2}],
            "last_fetched_at": "2026-07-19T12:00:00+00:00",
            "history": [{"fetched_at": "2026-07-19T12:00:00+00:00", "summary": "1 new entry(ies)", "data": [{"id": 2}]}],
        })

        resp = _run(mcp.handle_request({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "list_feed_entries", "arguments": {"feed_id": "feed-1"}},
        }))

        assert resp["result"]["isError"] is False
        payload = json.loads(resp["result"]["content"][0]["text"])
        assert payload["feed_id"] == "feed-1"
        assert payload["last_fetched_at"] == "2026-07-19T12:00:00+00:00"
        assert payload["last_response"] == [{"id": 1}, {"id": 2}]
        assert len(payload["history"]) == 1

    def test_missing_history_defaults_to_empty_list(self):
        _write_cache("feed-2", {"last_response": {"a": 1}, "last_fetched_at": "t", "history": []})

        resp = _run(mcp.handle_request({
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "list_feed_entries", "arguments": {"feed_id": "feed-2"}},
        }))

        payload = json.loads(resp["result"]["content"][0]["text"])
        assert payload["history"] == []


class TestJsonRpcDispatch:
    def test_initialize(self):
        resp = _run(mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
        assert resp["result"]["serverInfo"]["name"] == "aw-feed-subscriber"

    def test_notifications_initialized_returns_none(self):
        assert _run(mcp.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"})) is None

    def test_tools_list_exposes_list_feed_entries(self):
        resp = _run(mcp.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
        names = [t["name"] for t in resp["result"]["tools"]]
        assert names == ["list_feed_entries"]

    def test_tools_call_unknown_tool_is_error(self):
        resp = _run(mcp.handle_request({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "not_a_tool", "arguments": {}},
        }))
        assert resp["result"]["isError"] is True
        assert "Unknown tool" in resp["result"]["content"][0]["text"]

    def test_unknown_method_returns_error(self):
        resp = _run(mcp.handle_request({"jsonrpc": "2.0", "id": 5, "method": "not/a/method"}))
        assert resp["error"]["code"] == -32601
