// Integrated-mode entrypoint — dynamic-imported by aw-workspace-ui's
// loadComponentPlugin() once this app is installed with "ui:code" granted.
// Built by `npm run build` -> ui/dist/feed-subscriber-ui.mjs, referenced
// from aw-app.json's contributes.frontend.bundle. Same register(host)/JSX-
// factory pattern as aw-app-whiteboard/aw-app-presentations — see those
// files' header comments for the full host.h/host.React explanation.
//
// Ports agentic-workspace's FeedSubscriberTab.jsx (190 lines, a single
// "Save" button PUTting the whole feed_subscriber.cards array to
// /api/settings/aw) onto this app's own per-card CRUD routes
// (routes.py: GET/POST/PUT/DELETE /cards) — there is no single blob to
// save here, because auth_token has to be stripped and routed to the
// secret store before the rest of a card is ever persisted (see routes.py's
// module docstring). Each card gets its own Save (POST on first save, PUT
// after) and Remove (DELETE, immediate — no separate "Save" step) instead
// of one page-level Save button.
//
// core.window.body:feed-subscriber.main — a singleton window (unlike
// aw-app-presentations' one-window-per-id), so no windowKey/instanceId
// plumbing is needed; see aw-app-whiteboard's plugin.jsx for that contrast.

export function register(host) {
  const { useState, useEffect, useCallback } = host.React;

  const EMPTY_CARD = () => ({
    id: `feed-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    name: '',
    source_url: '',
    poll_interval_seconds: 60,
    call_type: 'webhook',
    webhook_url: '',
    auth_token: '',
    agent_slug: '',
    target_slug: 'w-feed-subscriber',
    _isNew: true,
  });

  function FeedSubscriberWindowBody() {
    const [cards, setCards] = useState(null);
    const [agents, setAgents] = useState([]);
    const [message, setMessage] = useState('');

    const showMessage = useCallback((msg) => {
      setMessage(msg);
      setTimeout(() => setMessage(''), 3000);
    }, []);

    const load = useCallback(async () => {
      const r = await host.sdk.api.fetch(host.app.apiUrl('/cards'));
      const data = await r.json();
      setCards((data.cards || []).map((c) => ({ ...c, auth_token: '' })));
    }, []);

    useEffect(() => { load(); }, [load]);

    useEffect(() => {
      host.sdk.api.fetch(host.app.apiUrl('/agents'))
        .then((r) => r.json())
        .then((data) => setAgents(data.agents || []))
        .catch(() => setAgents([]));
    }, []);

    const updateCard = useCallback((id, field, value) => {
      setCards((prev) => prev.map((c) => (c.id === id
        ? { ...c, [field]: field === 'poll_interval_seconds' ? Number(value) || 0 : value }
        : c)));
    }, []);

    const addCard = useCallback(() => setCards((prev) => [...prev, EMPTY_CARD()]), []);

    const saveCard = useCallback(async (card) => {
      const { _isNew, has_auth_token, ...body } = card;
      if (!body.auth_token) delete body.auth_token;
      try {
        const res = await host.sdk.api.fetch(
          host.app.apiUrl(_isNew ? '/cards' : `/cards/${encodeURIComponent(card.id)}`),
          {
            method: _isNew ? 'POST' : 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          },
        );
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          showMessage(err.detail || `Save failed (${res.status})`);
          return;
        }
        showMessage('Saved');
        await load();
      } catch (err) {
        showMessage(err.message || 'Save failed');
      }
    }, [load, showMessage]);

    const removeCard = useCallback(async (card) => {
      if (card._isNew) {
        setCards((prev) => prev.filter((c) => c.id !== card.id));
        return;
      }
      await host.sdk.api.fetch(host.app.apiUrl(`/cards/${encodeURIComponent(card.id)}`), { method: 'DELETE' });
      await load();
    }, [load]);

    if (cards === null) {
      return <div className="p-6 text-[var(--color-text-muted)]">Loading...</div>;
    }

    return (
      <div className="p-6 max-w-3xl h-full overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">Feed Subscriber</h2>
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              Each card polls a JSON source URL on its own interval. When the response changes since the
              last poll, it fires a Webhook Call or an Agent Call with the new entry.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {message && <span className="text-xs text-[var(--color-text-muted)]">{message}</span>}
            <button onClick={addCard}
              className="px-3 py-1.5 text-xs rounded border border-[var(--color-border)] text-[var(--color-text-primary)] hover:bg-[var(--color-bg-secondary)] transition-colors">
              + Add feed
            </button>
          </div>
        </div>

        {cards.length === 0 && (
          <div className="text-xs text-[var(--color-text-muted)] italic">
            No feed subscriptions yet — click "+ Add feed" to create one.
          </div>
        )}

        <div className="space-y-4">
          {cards.map((card) => (
            <FeedCard
              key={card.id}
              card={card}
              agents={agents}
              onChange={(field, value) => updateCard(card.id, field, value)}
              onSave={() => saveCard(card)}
              onRemove={() => removeCard(card)}
            />
          ))}
        </div>
      </div>
    );
  }

  function FeedCard({ card, agents, onChange, onSave, onRemove }) {
    return (
      <div className="bg-[var(--color-bg-secondary)] rounded-lg border border-[var(--color-border)] p-4 space-y-3">
        <div className="flex items-center justify-between">
          <input
            value={card.name}
            onChange={(e) => onChange('name', e.target.value)}
            placeholder="Feed name"
            className="text-sm font-medium bg-transparent text-[var(--color-text-primary)] border-b border-transparent hover:border-[var(--color-border)] focus:border-[var(--color-accent)] outline-none px-1"
          />
          <div className="flex items-center gap-3">
            <button onClick={onSave}
              className="text-xs px-2 py-1 rounded bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-hover)] transition-colors">
              Save
            </button>
            <button onClick={onRemove}
              className="text-xs text-[var(--color-text-muted)] hover:text-red-400 transition-colors">
              Remove
            </button>
          </div>
        </div>

        <Field label="Source JSON URL" value={card.source_url}
          onChange={(v) => onChange('source_url', v)}
          placeholder="https://example.com/feed.json" />
        <Field label="Poll Interval (seconds)" value={card.poll_interval_seconds}
          onChange={(v) => onChange('poll_interval_seconds', v)}
          type="number" placeholder="60" />

        <label className="block">
          <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Call type</span>
          <select
            value={card.call_type}
            onChange={(e) => onChange('call_type', e.target.value)}
            className="mt-0.5 w-full bg-[var(--color-bg-primary)] text-sm text-[var(--color-text-primary)] border border-[var(--color-border)] rounded px-2 py-1.5 outline-none focus:border-[var(--color-accent)] transition-colors"
          >
            <option value="webhook">Webhook Call</option>
            <option value="agent">Agent Call</option>
          </select>
        </label>

        {card.call_type === 'agent' ? (
          <>
            <label className="block">
              <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Agent</span>
              <select
                value={card.agent_slug}
                onChange={(e) => onChange('agent_slug', e.target.value)}
                className="mt-0.5 w-full bg-[var(--color-bg-primary)] text-sm text-[var(--color-text-primary)] border border-[var(--color-border)] rounded px-2 py-1.5 outline-none focus:border-[var(--color-accent)] transition-colors"
              >
                <option value="">Select an agent…</option>
                {agents.map((a) => (
                  <option key={a.slug} value={a.slug}>{a.name || a.slug}</option>
                ))}
              </select>
            </label>
            <Field label="Target slug" value={card.target_slug}
              onChange={(v) => onChange('target_slug', v)}
              placeholder="w-feed-subscriber" />
          </>
        ) : (
          <>
            <Field label="Target Webhook URL" value={card.webhook_url}
              onChange={(v) => onChange('webhook_url', v)}
              placeholder="https://agents-platform.internal/api/webhooks/..." />
            <Field
              label={card.has_auth_token ? 'Auth Token (Bearer) — set, leave blank to keep' : 'Auth Token (Bearer)'}
              value={card.auth_token}
              onChange={(v) => onChange('auth_token', v)}
              type="password" placeholder="token used as Authorization: Bearer <token>" />
          </>
        )}
      </div>
    );
  }

  function Field({ label, value, onChange, type = 'text', placeholder }) {
    return (
      <label className="block">
        <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">{label}</span>
        <input
          type={type}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="mt-0.5 w-full bg-[var(--color-bg-primary)] text-sm text-[var(--color-text-primary)] border border-[var(--color-border)] rounded px-2 py-1.5 outline-none focus:border-[var(--color-accent)] transition-colors"
        />
      </label>
    );
  }

  host.registerWindow('feed-subscriber.main', FeedSubscriberWindowBody);
}

export default register;
