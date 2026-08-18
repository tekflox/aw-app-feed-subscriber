"""feed_subscriber_app's mode-agnostic FastAPI sub-app (ADR Decision 2/6),
mounted by ``plugin.py`` at ``/api/apps/feed-subscriber``.

Card CRUD (``/cards``) is a DEDICATED surface rather than the generic
``POST /api/apps/feed-subscriber/config`` JSON editor, for one reason:
``auth_token`` must never sit in plain config (per Frederico's instruction,
same rule ``aw-app-notion`` follows for its own ``notion_token`` — see
``ctx.secrets`` usage below). ``config_schema.cards`` items carry no
``auth_token`` property at all; every write here strips it before the rest
of the card is persisted, and stores it separately keyed
``card:<id>:auth_token``.

Persisting the redacted card list itself goes through core's own generic
``POST /api/apps/feed-subscriber/config`` route via an authenticated
self-loopback call (``AW_WORKSPACE_API_KEY`` / ``X-Api-Key`` — see
``docs/app-workspace-api-auth.md`` in aw-app-template) rather than mutating
``ctx.config`` in place: apps don't import ``src.apps.*``/``src.api.*``
directly, so there is no in-process function to call instead, and the core
route is also what keeps the durable config-store snapshot, the cloud
registry mirror, and ``contributes.mcp.reload_on_save`` all in sync — doing
any less here would only look like it worked until the next reinstall.

``agents_platform_base``/``agents_platform_token`` (Agent Call dispatch +
the agent-picker this app's UI uses) stay plain ``config_schema`` fields
read straight off ``ctx.config`` — same pattern
``aw-app-agents-platform-runners`` uses for its own identically-named
fields (``x-secret: true`` there is a UI-masking hint only, not a
secret-store route; only the per-card ``auth_token`` above gets the
stronger treatment).
"""

from __future__ import annotations

import os

import httpx
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response

from . import poller as poller_mod


def _card_secret_key(card_id: str) -> str:
    return f"card:{card_id}:auth_token"


def _self_base_url() -> str:
    port = int(os.environ.get("AW_PORT") or 9030)
    return f"http://127.0.0.1:{port}"


def _redact(card: dict) -> dict:
    out = dict(card)
    out.pop("auth_token", None)
    return out


async def _save_cards(cards: list[dict]) -> None:
    api_key = os.environ.get("AW_WORKSPACE_API_KEY")
    headers = {"X-Api-Key": api_key} if api_key else {}
    url = f"{_self_base_url()}/api/apps/feed-subscriber/config"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json={"config": {"cards": cards}}, headers=headers)
        resp.raise_for_status()


def build_routes(ctx) -> FastAPI:
    """Mode-agnostic factory. ``ctx`` is the live ``AppContext`` — routes
    always read ``ctx.config`` fresh (never a captured snapshot) so a
    config save from ANY path (this app's own ``/cards`` routes, or a
    direct edit of the generic JSON config panel) is visible on the very
    next request, no restart needed."""
    app = FastAPI(title="feed-subscriber")

    def _cards() -> list[dict]:
        return [dict(c) for c in ((ctx.config or {}).get("cards") or [])]

    @app.get("/cards")
    async def list_cards() -> dict:
        cards = []
        for c in _cards():
            card = dict(c)
            card["has_auth_token"] = bool(ctx.secrets.read(_card_secret_key(c.get("id", ""))))
            cards.append(card)
        return {"cards": cards}

    @app.post("/cards")
    async def create_card(payload: dict = Body(...)) -> dict:
        card = dict(payload)
        card_id = (card.get("id") or "").strip()
        if not card_id:
            raise HTTPException(400, "id is required")
        if any(c.get("id") == card_id for c in _cards()):
            raise HTTPException(409, f"card {card_id!r} already exists")
        auth_token = (card.pop("auth_token", "") or "").strip()
        if auth_token:
            ctx.secrets.write(_card_secret_key(card_id), auth_token)
        cards = _cards() + [_redact(card)]
        await _save_cards(cards)
        return {"card": _redact(card), "has_auth_token": bool(auth_token)}

    @app.put("/cards/{card_id}")
    async def update_card(card_id: str, payload: dict = Body(...)) -> dict:
        cards = _cards()
        idx = next((i for i, c in enumerate(cards) if c.get("id") == card_id), None)
        if idx is None:
            raise HTTPException(404, f"card {card_id!r} not found")
        incoming = dict(payload)
        incoming["id"] = card_id
        if "auth_token" in incoming:
            token = (incoming.pop("auth_token") or "").strip()
            if token:
                ctx.secrets.write(_card_secret_key(card_id), token)
            else:
                ctx.secrets.delete(_card_secret_key(card_id))
        cards[idx] = _redact(incoming)
        await _save_cards(cards)
        card = dict(cards[idx])
        card["has_auth_token"] = bool(ctx.secrets.read(_card_secret_key(card_id)))
        return {"card": card}

    @app.delete("/cards/{card_id}")
    async def delete_card(card_id: str) -> dict:
        cards = [c for c in _cards() if c.get("id") != card_id]
        ctx.secrets.delete(_card_secret_key(card_id))
        poller_mod.delete_cache(card_id)
        await _save_cards(cards)
        return {"deleted": card_id}

    @app.get("/agents")
    async def list_agents() -> dict:
        """Proxy for the config UI's Agent dropdown — this app's own
        agents_platform_base/token, not the caller's. Failures degrade to
        an empty list (with the reason attached) rather than a 500, since a
        picker with no options is still a usable form; the base/token
        config fields just haven't been set yet."""
        base = (ctx.config or {}).get("agents_platform_base") or poller_mod.DEFAULT_AGENTS_PLATFORM_BASE
        token = (ctx.config or {}).get("agents_platform_token")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{base.rstrip('/')}/api/agents", headers=headers)
            resp.raise_for_status()
            data = resp.json()
            agents = data.get("agents", data) if isinstance(data, dict) else data
            return {"agents": agents}
        except Exception as exc:  # noqa: BLE001 — surfaced to the picker, not a route failure
            return {"agents": [], "error": str(exc)}

    # MCP — Streamable HTTP, auto-discovered by aw-mcp-gateway's app-scan
    # (see mcp/self_register.py + mcp/http_handler.py).
    @app.post("/mcp")
    async def mcp_post(data: dict | list = Body(...)):
        from .mcp.http_handler import handle_request as mcp_handle_request

        messages = data if isinstance(data, list) else [data]
        responses = []
        for m in messages:
            r = await mcp_handle_request(m)
            if r is not None:
                responses.append(r)
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses if isinstance(data, list) else responses[0])

    @app.get("/mcp")
    async def mcp_get():
        return Response(status_code=405)

    return app
