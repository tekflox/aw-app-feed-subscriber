"""MCP server for Feed Subscriber, exposed over Streamable HTTP (POST /mcp) —
same in-process pattern as ``aw-app-presentations``' and ``aw-app-whiteboard``'s
own ``mcp/http_handler.py`` (see ``self_register.py`` for why HTTP-in-process
replaced an earlier stdio design workspace-wide: a stdio child spawned by
aw-mcp-gateway has no path to this app's own state).

Ported from agentic-workspace's ``src/mcp/feed_subscriber.py``: a single
tool, ``list_feed_entries`` — a read-only window over the poller's cache
files (``poller.py``), no network call of its own.
"""

from __future__ import annotations

import json

from .. import poller as poller_mod

TOOLS_SCHEMA = [
    {
        "name": "list_feed_entries",
        "description": (
            "List the cached state of a Feed Subscriber card: the last polled "
            "response and the history of the last N detected changes (new "
            "entries). Use to see what a feed subscriber card has captured "
            "without re-polling the source."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "feed_id": {
                    "type": "string",
                    "description": "The subscription card's id, as configured in the Feed Subscriber window.",
                },
            },
            "required": ["feed_id"],
        },
    },
]


def _ok(req_id, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}], "isError": False}}


def _err(req_id, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}], "isError": True}}


def _list_feed_entries(req_id, args: dict) -> dict:
    feed_id = args.get("feed_id")
    if not feed_id:
        return _err(req_id, "feed_id is required")

    try:
        with open(poller_mod.cache_path(feed_id)) as f:
            cache = json.load(f)
    except FileNotFoundError:
        return _err(req_id, f"No cache found for feed_id '{feed_id}' — has it polled at least once yet?")
    except json.JSONDecodeError as exc:
        return _err(req_id, f"Cache file for '{feed_id}' is corrupt: {exc}")

    result = {
        "feed_id": feed_id,
        "last_fetched_at": cache.get("last_fetched_at"),
        "last_response": cache.get("last_response"),
        "history": cache.get("history", []),
    }
    return _ok(req_id, json.dumps(result, indent=2))


async def handle_request(request: dict) -> dict | None:
    method = request.get("method", "")
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aw-feed-subscriber", "version": "1.0.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS_SCHEMA}}

    if method != "tools/call":
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}

    name = request.get("params", {}).get("name", "")
    args = request.get("params", {}).get("arguments", {}) or {}

    if name == "list_feed_entries":
        return _list_feed_entries(req_id, args)

    return _err(req_id, f"Unknown tool: {name}")
