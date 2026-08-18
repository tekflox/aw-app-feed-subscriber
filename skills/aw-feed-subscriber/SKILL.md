---
name: aw-feed-subscriber
description: What a Feed Subscriber card is, when to configure Webhook Call vs Agent Call, how to read a card's cached state with list_feed_entries, and how to diagnose a feed that stopped firing. Use whenever asked to set up, inspect, or debug a Feed Subscriber card, or when list_feed_entries is available and relevant to the task.
---

# aw-feed-subscriber — polling a JSON feed into a webhook or an agent

This skill did not exist in the agentic-workspace monolith — the monolith
had the poller, the MCP tool, and the config UI, but no skill teaching an
agent how to use any of it. This is that missing piece, written for
`aw-app-feed-subscriber`.

## What a card is

A **card** is one subscription: a JSON `source_url` polled on its own
`poll_interval_seconds`, independent of every other card's interval or
failures. Configured through the **Feed Subscriber** window (Apps grid),
which talks to this app's own CRUD routes
(`/api/apps/feed-subscriber/cards`) — there is no `aw.json`/config-blob
save here the way the monolith had; each card is created/edited/deleted
individually.

Fields:

- `id`, `name` — identity, free text.
- `source_url` — a JSON endpoint. Polled with a plain `GET`, no auth of its
  own (if the *source* needs auth, this app has no field for that today —
  only the dispatch side, below, carries a token).
- `poll_interval_seconds` — this card's own cadence. Not shared with any
  other card.
- `call_type` — `"webhook"` or `"agent"` (see below).

On every poll the fetched JSON is compared against the last cached
response. **The first-ever poll never dispatches** — there is nothing to
diff against yet, so it just seeds the cache. A dispatch only fires on poll
N+1 once poll N's response is in the cache and poll N+1's differs.

## Webhook Call vs Agent Call

**Webhook Call** (`webhook_url` + optional `auth_token`): a plain
`POST {"feed": <name>, "message": <diff summary>}`, with
`Authorization: Bearer <auth_token>` if a token is set. Use this when the
receiving side is not itself an agent — a CI hook, another service's
ingest endpoint, a Slack/Discord webhook shim.

**Agent Call** (`agent_slug` + `target_slug`): `POST
/api/agents/{agent_slug}/run` on agents-platform (this app's own
`agents_platform_base`/`agents_platform_token` config — set once for the
whole app, not per card), with the diff summary as `input`. This is
**wakeup-style**: the dispatch does not tell the agent what to do, it just
hands it a description of what changed and lets the agent decide. Use this
when reacting to the change needs judgment (summarize it, file a card,
notify someone conditionally) rather than a fixed action a webhook receiver
already encodes.

`target_slug` defaults to `w-feed-subscriber` if left blank — override it
to route the wakeup into a specific existing Target instead of a fresh one
each time.

## `auth_token` never appears in plain config

A card's `auth_token` (webhook Bearer credential) is written to this app's
own secret store the moment it's saved (`ctx.secrets`, keyed
`card:<id>:auth_token`) and stripped before the rest of the card is
persisted. `GET /cards` reports `has_auth_token: true/false`, never the
value. Same rule `aw-app-notion` follows for its own `notion_token` — if
you're reading this because a card's webhook stopped authenticating, the
token is not visible anywhere in config, only in the encrypted per-app
secret file; re-enter it through the Feed Subscriber window to rotate it.

## Reading a card's captured state — `list_feed_entries`

```
list_feed_entries(feed_id="<card id>")
```

Read-only, no network call of its own — a window onto the cache the poller
already wrote. Returns:

- `last_fetched_at` — when the source was last successfully polled.
- `last_response` — the full last-seen payload.
- `history` — up to the last 50 *changed* polls (not every poll — only the
  ones that differed from the previous one), each with `fetched_at`,
  `summary` (best-effort diff description), and `data` (the new payload at
  that point).

Use this to check what a card has actually seen before assuming the poller
is broken — a feed with a stable source (nothing new posted) will show a
recent `last_fetched_at` and an empty (or old) `history`, which is correct
behavior, not a bug.

## Diagnosing a feed that stopped firing

Work through these in order — each rules out a different half of the
pipeline (fetch side vs. dispatch side):

1. **Is it polling at all?** `list_feed_entries` — if `last_fetched_at` is
   null or very stale (older than several `poll_interval_seconds`), the
   poll loop for this card isn't running. Check that the card still exists
   (`GET /api/apps/feed-subscriber/cards`) — a deleted-then-recreated card
   gets a fresh cache with no history, which looks identical to "never
   polled" from `list_feed_entries` alone.
2. **Is `last_fetched_at` recent but `history` never grows?** The source is
   genuinely not changing, or is returning content that happens to
   deep-equal the previous poll every time (e.g. a field that re-orders but
   doesn't actually change, or a timestamp the source itself doesn't bump).
   Not a bug in this app — verify by hand what the `source_url` actually
   returns.
3. **`history` has fresh entries but the webhook/agent never receives
   anything?** The fetch/diff half is working; the dispatch half is
   failing silently (a dead webhook, an expired token, a `target_slug` no
   longer valid) — this app logs every dispatch attempt and its outcome
   (`aw-workspace-cli logs` for the aw-workspace container, since this is a
   Tier-1 in-process app with no separate container of its own) but never
   raises, by design: one card's dead webhook must never take down another
   card's polling. Check the log line for this card's name.
4. **Card configured as Agent Call and nothing happens?** Confirm
   `agents_platform_base`/`agents_platform_token` are set in this app's own
   config (Settings), not just the card's `agent_slug` — a missing base
   silently falls back to the default gateway address, which may not be
   the instance you meant.

## Cache location

The cache is durable state, not scratch — it lives under
`AW_WORKSPACE_HOME/data/feed-subscriber/<card_id>.json`, survives an app
reinstall. Deleting a card removes its cache file (`DELETE
/cards/{id}`); losing that file any other way (manual edit, disk issue)
makes the next poll treat the current response as a fresh baseline and
re-fires nothing until the poll *after* that — not a false dispatch, but a
missed one for whatever changed between the lost baseline and the next
poll.
