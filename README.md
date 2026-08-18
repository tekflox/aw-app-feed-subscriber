# aw-app-feed-subscriber

Ports agentic-workspace's Feed Subscriber into aw-workspace — a full port,
not just the MCP tool: the poller, the config UI, and a new skill (the
monolith never had one for this feature).

| Monolith | This app |
|---|---|
| `src/start/feed_subscriber.py` (own OS process, `./aw start feed-subscriber`) | `feed_subscriber_app/poller.py` — one `ctx.watchdog` task (`reconcile`, 10s cadence) supervising a plain `asyncio.Task` per card, same per-card-interval design |
| `src/mcp/feed_subscriber.py` (`list_feed_entries`, stdio) | `feed_subscriber_app/mcp/http_handler.py` — same tool, in-process HTTP MCP (`mcp/self_register.py` registers it with aw-mcp-gateway) |
| `src/app/src/components/FeedSubscriberTab.jsx` (Settings tab, single Save button PUTting the whole `feed_subscriber.cards` array) | `ui/src/plugin.jsx` — a component-mode window (`feed-subscriber.main`), per-card CRUD against this app's own routes |
| `src/config/aw.json` → `feed_subscriber.cards[]` | `aw-app.json`'s `config_schema.cards` (no `auth_token` in the schema — see below), managed through `/api/apps/feed-subscriber/cards` |
| `.tmp/feed-subscriber/<card>.json` | `AW_WORKSPACE_HOME/data/feed-subscriber/<card>.json` — durable, survives reinstall (`.tmp/` here is scratch, not a diff baseline) |
| *(no skill)* | `skills/aw-feed-subscriber/SKILL.md` — new |

## What it does

Each configured **card** polls a JSON `source_url` on its own interval,
independent of every other card. When the response changes since the last
poll, it fires one of:

- **Webhook Call** — `POST {"feed", "message"}` to `webhook_url`, with
  `Authorization: Bearer <auth_token>` if set.
- **Agent Call** — `POST /api/agents/{agent_slug}/run` on agents-platform
  with the diff as `input` (wakeup-style — the agent decides what to do).

See `skills/aw-feed-subscriber/SKILL.md` for the full behavior contract,
including how to diagnose a card that stopped firing.

## Configuration

Open the **Feed Subscriber** window from the Apps grid to add/edit/remove
cards. Two app-level settings (Settings → Feed Subscriber) are shared by
every Agent Call card:

- `agents_platform_base` — defaults to the bridge-gateway address every
  other `aw-app-agents-platform-runners`-style app on this workspace uses.
- `agents_platform_token` — optional bearer token for that instance.

A card's `auth_token` is never stored in plain config — it's routed to this
app's own secret store the moment the card is saved (`ctx.secrets`, keyed
`card:<id>:auth_token`), same rule `aw-app-notion` follows for its own
`notion_token`.

## Layout

- `aw-app.json` — the manifest (`id: feed-subscriber`, `tier: inprocess`).
- `feed_subscriber_app/poller.py` — cache I/O, diff detection,
  webhook/agent dispatch, and `PollerSupervisor` (the `ctx.watchdog`-backed
  per-card task supervisor).
- `feed_subscriber_app/routes.py` — card CRUD, the agent-picker proxy
  (`GET /agents`), and the MCP-over-HTTP mount.
- `feed_subscriber_app/mcp/` — `list_feed_entries` (`http_handler.py`) +
  gateway self-registration (`self_register.py`).
- `feed_subscriber_app/plugin.py` — `FeedSubscriberAppPlugin` entrypoint.
- `ui/src/plugin.jsx` — the Feed Subscriber window body (component mode).
- `tests/` — `validate_manifest.py`, `test_poller.py`, `test_routes.py`,
  `test_mcp_server.py`.
- `skills/aw-feed-subscriber/SKILL.md`.

## CI/CD

`tests/validate_manifest.py` and `tests/test_*.py` run in
`tekflox/aw-marketplace`'s shared `app-release.yml` on every push to
`master` — a failure stops the release before any version bump, tag, or
marketplace catalog sync happens.
