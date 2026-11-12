"""Entrypoint referenced by aw-app.json's runtime.entrypoint
("feed_subscriber_app.plugin:FeedSubscriberAppPlugin").

Ports agentic-workspace's Feed Subscriber (5 files — poller process, MCP
tool, config UI, aw.json config, tests) onto the F4 ``ctx`` facades:

* ``ctx.routes`` (``routes:register``) — card CRUD + agent picker + MCP-
  over-HTTP, mounted at ``/api/apps/feed-subscriber`` (``routes.py``).
* ``ctx.watchdog`` (``watchdog:tasks``) — ONE registered task,
  ``poller.PollerSupervisor.reconcile``, replacing the monolith's own OS
  process (``./aw start feed-subscriber``) — see ``poller.py``'s module
  docstring for why this is a supervisor over asyncio sub-tasks rather than
  N watchdog registrations.
* ``ctx.secrets`` (``secrets:own``) — per-card ``auth_token``, keyed
  ``card:<id>:auth_token`` (never in plain config — see ``routes.py``).

Also self-registers an in-process MCP-over-HTTP endpoint (``mcp/`` —
same pattern as ``aw-app-presentations``/``aw-app-whiteboard``) so
aw-mcp-gateway discovers ``list_feed_entries`` with no manual wiring.
"""

from __future__ import annotations

import logging
import os

from . import poller as poller_mod
from . import routes as routes_mod
from .mcp import self_register as mcp_self_register

log = logging.getLogger("aw_apps.feed_subscriber")


def cards_with_secrets(cards: list[dict], secrets) -> list[dict]:
    """Merge each card's ``auth_token`` back in from the secret store it was
    stripped into by ``routes.py`` on every write. The poller needs the real
    token to put it on the ``Authorization`` header; ``ctx.config`` (and
    anything reading cards straight off it, e.g. the ``/cards`` API) must
    never see it in plain config."""
    merged = []
    for card in cards:
        card = dict(card)
        card_id = card.get("id")
        if card_id:
            token = secrets.read(routes_mod._card_secret_key(card_id))
            if token:
                card["auth_token"] = token
        merged.append(card)
    return merged


class FeedSubscriberAppPlugin:
    async def activate(self, ctx) -> None:
        self.ctx = ctx

        ctx.routes.register(routes_mod.build_routes(ctx))

        port = int(os.environ.get("AW_PORT", "9030"))
        mcp_self_register.register_self(ctx.package_dir, port)

        def _get_cards() -> list[dict]:
            return list((ctx.config or {}).get("cards") or [])

        def _get_cards_for_poller() -> list[dict]:
            return cards_with_secrets(_get_cards(), ctx.secrets)

        def _get_agents_platform() -> tuple[str, str | None]:
            cfg = ctx.config or {}
            base = cfg.get("agents_platform_base") or poller_mod.DEFAULT_AGENTS_PLATFORM_BASE
            return base, cfg.get("agents_platform_token")

        self._supervisor = poller_mod.PollerSupervisor(_get_cards_for_poller, _get_agents_platform)
        ctx.watchdog.register("reconcile", self._supervisor.reconcile,
                              poller_mod.RECONCILE_INTERVAL_S, run_immediately=True)

        log.info("aw-app-feed-subscriber activated: %d card(s) configured, routes mounted",
                 len(_get_cards()))

    async def on_config_saved(self, ctx) -> None:
        """Nothing to rebuild by hand — ``routes.py``/``poller.py`` both read
        ``ctx.config`` live on every call/reconcile tick. This exists purely
        to log so a card add/remove is visible without grepping app logs for
        the next reconcile tick (10s later, easy to miss)."""
        cards = (ctx.config or {}).get("cards") or []
        log.info("aw-app-feed-subscriber config saved: %d card(s) configured",
                 len(cards))

    async def deactivate(self) -> None:
        supervisor = getattr(self, "_supervisor", None)
        if supervisor is not None:
            supervisor.cancel_all()
        log.info("aw-app-feed-subscriber deactivated")
