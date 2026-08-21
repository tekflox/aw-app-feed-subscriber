---
repo: architecture
path: docs/architecture/aw-app-feed-subscriber.md
source: generated
edited: false
checksum: sha256:5143338d301c97ebf147b9a896db7de408b67dee0282640cfefba4f78253a2d7
---
# Feed Subscriber

- **repo**: aw-app-feed-subscriber
- **layer**: app
- **technologies**: python, react
- **health** (derived): planned

Ports agentic-workspace's Feed Subscriber into aw-workspace: an asyncio poller — one independent task per configured "card" — watches a JSON source URL on its own interval and, when the response changes since the last poll, fires a Webhook Call or an Agent Call (POST /api/agents/{slug}/run on agents-platform) describing what changed. The last polled response and change history per card are exposed read-only through the list_feed_entries MCP tool. Configuration is a component-mode CRUD window (Settings → Feed Subscriber); each card's auth_token is routed to the zero-knowledge secret store, never stored in plain config.

## Connections
- `http` → **aw-workspace** — routes mounted at /api/apps/feed-subscriber
- `stdio-mcp` → **mcp-gateway** — MCP surface aggregated by the gateway

## MCP tools
- `list_feed_entries`

## Requirements
_none documented_
