"""The Feed Subscriber poller — one independent asyncio task per configured
card, ported from agentic-workspace's ``src/start/feed_subscriber.py``.

Each card polls its ``source_url`` on its own ``poll_interval_seconds``. On
every poll the fetched JSON is compared against the last cached snapshot; on
a diff it fires a Webhook Call (POST to ``webhook_url``, Bearer
``auth_token`` if set) or an Agent Call (``POST /api/agents/{slug}/run`` on
agents-platform, wakeup-style — the agent decides what to do with the new
entry).

Two things changed from the monolith port, both load-bearing:

1. **Cache location.** The monolith wrote to ``.tmp/feed-subscriber/`` —
   here ``.tmp/`` is scratch, wiped on container recreation, and this cache
   is the diff baseline, not scratch. Losing it makes the next poll treat
   everything as new and re-fire every webhook/agent call. It lives under
   ``AW_WORKSPACE_HOME/data/feed-subscriber/`` instead (see
   ``_workspace_home`` below) — the documented location for state that must
   survive an app reinstall.
2. **Lifecycle.** The monolith ran this as its own OS process
   (``./aw start feed-subscriber``), with ``_poll_loop()`` as a bare
   ``while True`` reconciling tasks against ``aw.json``. Here there is no
   process manager for a Tier-1 (in-process) app — ``FeedSubscriberAppPlugin``
   registers ``reconcile()`` as a single ``ctx.watchdog`` task (interval
   10s, matching the monolith's own reconcile cadence) that does exactly
   what ``_poll_loop``'s body did: start a ``_poll_card`` task for every new
   card id, cancel the task for every id that disappeared from config. The
   card tasks themselves are plain ``asyncio.create_task`` calls (not
   watchdog-registered — watchdog tasks are fixed-callback, these are
   long-running loops with their own internal sleep), so the plugin keeps
   its own ``dict[str, asyncio.Task]`` and cancels every entry in
   ``deactivate()`` — ``ctx.watchdog``'s own cancel-on-uninstall only
   reaches the ``reconcile`` task it registered, not the sub-tasks that
   task spawned.

Cards read from live config (a callable, not a snapshot) so a card added or
edited through the UI takes effect on ``reconcile``'s very next tick — no
app or workspace restart needed, same reasoning as
``aw-app-agents-platform-runners``'s ``self._live_config``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Callable

import httpx

log = logging.getLogger("aw_apps.feed_subscriber.poller")

DEFAULT_POLL_INTERVAL_SECONDS = 60
MAX_HISTORY_ENTRIES = 50
DEFAULT_AGENTS_PLATFORM_BASE = "http://172.18.0.1:10014"
RECONCILE_INTERVAL_S = 10.0


def _workspace_home() -> str:
    home = os.environ.get("AW_WORKSPACE_HOME")
    if home:
        return home
    root = os.environ.get("AW_WORKSPACE_CONTAINER_DIR", "/opt/aw-workspace")
    return os.path.join(root, ".aw-workspace")


CACHE_DIR = os.path.join(_workspace_home(), "data", "feed-subscriber")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_id(card_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(card_id))


def cache_path(card_id: str) -> str:
    return os.path.join(CACHE_DIR, f"{safe_id(card_id)}.json")


def load_cache(card_id: str) -> dict:
    try:
        with open(cache_path(card_id)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_response": None, "last_fetched_at": None, "history": []}


def save_cache(card_id: str, cache: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_path(card_id), "w") as f:
        json.dump(cache, f, indent=2)


def delete_cache(card_id: str) -> None:
    try:
        os.remove(cache_path(card_id))
    except FileNotFoundError:
        pass


def diff_summary(old, new) -> str:
    """Best-effort human-readable description of what changed."""
    if isinstance(old, list) and isinstance(new, list):
        old_set = {json.dumps(item, sort_keys=True) for item in old}
        added = [item for item in new if json.dumps(item, sort_keys=True) not in old_set]
        if added:
            return f"{len(added)} new entry(ies): {json.dumps(added)[:2000]}"
    return f"content changed: {json.dumps(new)[:2000]}"


async def dispatch_webhook(client: httpx.AsyncClient, card: dict, message: str) -> None:
    url = (card.get("webhook_url") or "").strip()
    if not url:
        log.info("[%s] Webhook call configured but webhook_url is empty", card.get("name", card.get("id")))
        return
    headers = {"Content-Type": "application/json"}
    token = (card.get("auth_token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = await client.post(url, json={"feed": card.get("name"), "message": message}, headers=headers, timeout=15)
        log.info("[%s] Webhook call -> %s", card.get("name"), resp.status_code)
    except Exception as exc:  # noqa: BLE001 — a dead webhook must never crash the poller
        log.warning("[%s] Webhook call failed: %s", card.get("name"), exc)


async def dispatch_agent(client: httpx.AsyncClient, card: dict, message: str, *,
                          agents_platform_base: str, agents_platform_token: str | None) -> None:
    agent_slug = (card.get("agent_slug") or "").strip()
    if not agent_slug:
        log.info("[%s] Agent call configured but agent_slug is empty", card.get("name", card.get("id")))
        return
    target_slug = (card.get("target_slug") or "").strip() or "w-feed-subscriber"
    headers = {}
    if agents_platform_token:
        headers["Authorization"] = f"Bearer {agents_platform_token}"
    try:
        resp = await client.post(
            f"{agents_platform_base.rstrip('/')}/api/agents/{agent_slug}/run",
            json={"input": message, "target_slug": target_slug},
            headers=headers,
            timeout=15,
        )
        log.info("[%s] Agent call -> %s (%s): %s", card.get("name"), agent_slug, resp.status_code, resp.text[:200])
    except Exception as exc:  # noqa: BLE001 — a dead agents-platform must never crash the poller
        log.warning("[%s] Agent call failed: %s", card.get("name"), exc)


async def poll_card(card_id: str, get_card: Callable[[str], dict | None],
                     get_agents_platform: Callable[[], tuple[str, str | None]]) -> None:
    """One card's loop: fetch source_url on its own interval, diff, dispatch,
    cache. Returns (stopping the task) once the card disappears from config —
    ``reconcile`` below only starts a fresh task if the id reappears."""
    card = get_card(card_id)
    name = (card or {}).get("name") or card_id
    log.info("[%s] polling loop started", name)
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            card = get_card(card_id)
            if card is None:
                log.info("[%s] card removed from config — stopping", name)
                return
            name = card.get("name") or card_id

            source_url = (card.get("source_url") or "").strip()
            interval = card.get("poll_interval_seconds") or DEFAULT_POLL_INTERVAL_SECONDS

            if not source_url:
                log.info("[%s] no source_url configured — waiting...", name)
            else:
                try:
                    resp = await client.get(source_url)
                    resp.raise_for_status()
                    new_data = resp.json()
                    cache = load_cache(card_id)
                    old_data = cache.get("last_response")
                    changed = old_data is not None and new_data != old_data
                    cache["last_response"] = new_data
                    cache["last_fetched_at"] = _now()

                    if changed:
                        message = f"Feed {name}: nova entrada detectada: {diff_summary(old_data, new_data)}"
                        cache.setdefault("history", []).append({
                            "fetched_at": cache["last_fetched_at"],
                            "summary": diff_summary(old_data, new_data),
                            "data": new_data,
                        })
                        cache["history"] = cache["history"][-MAX_HISTORY_ENTRIES:]
                        log.info("[%s] change detected", name)

                        call_type = card.get("call_type") or "webhook"
                        if call_type == "agent":
                            base, token = get_agents_platform()
                            await dispatch_agent(client, card, message,
                                                  agents_platform_base=base, agents_platform_token=token)
                        else:
                            await dispatch_webhook(client, card, message)
                    else:
                        log.info("[%s] polled %s -> no change", name, source_url)

                    save_cache(card_id, cache)
                except Exception as exc:  # noqa: BLE001 — one bad poll must never kill this card's loop
                    log.warning("[%s] poll failed for %s: %s", name, source_url, exc)

            await asyncio.sleep(interval)


class PollerSupervisor:
    """Owns the dict of per-card ``asyncio.Task``s and the single reconcile
    tick — the thing ``plugin.py`` registers with ``ctx.watchdog`` and cancels
    everything from on ``deactivate()``."""

    def __init__(self, get_cards: Callable[[], list[dict]],
                 get_agents_platform: Callable[[], tuple[str, str | None]]) -> None:
        self._get_cards = get_cards
        self._get_agents_platform = get_agents_platform
        self._tasks: dict[str, "object"] = {}

    def _card_by_id(self, card_id: str) -> dict | None:
        for c in self._get_cards():
            if c.get("id") == card_id:
                return c
        return None

    async def reconcile(self) -> None:
        cards = {c.get("id"): c for c in self._get_cards() if c.get("id")}
        for card_id in cards:
            if card_id not in self._tasks:
                self._tasks[card_id] = asyncio.create_task(
                    poll_card(card_id, self._card_by_id, self._get_agents_platform))
        for card_id in list(set(self._tasks) - set(cards)):
            # Cancel only — never delete the cache here. This tick's config
            # read racing a concurrent config WRITE would otherwise look
            # exactly like a deliberate deletion and wipe the diff baseline
            # for a card that is still configured. Explicit deletion (the
            # DELETE /cards/{id} route) removes the cache itself.
            self._tasks.pop(card_id).cancel()

    def cancel_all(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()

    def task_ids(self) -> list[str]:
        return list(self._tasks)
