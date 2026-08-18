function S(e) {
  const { useState: g, useEffect: x, useCallback: i } = e.React, h = () => ({
    id: `feed-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    name: "",
    source_url: "",
    poll_interval_seconds: 60,
    call_type: "webhook",
    webhook_url: "",
    auth_token: "",
    agent_slug: "",
    target_slug: "w-feed-subscriber",
    _isNew: !0
  });
  function f() {
    const [r, c] = g(null), [o, d] = g([]), [u, a] = g(""), v = i((t) => {
      a(t), setTimeout(() => a(""), 3e3);
    }, []), p = i(async () => {
      const l = await (await e.sdk.api.fetch(e.app.apiUrl("/cards"))).json();
      c((l.cards || []).map((n) => ({ ...n, auth_token: "" })));
    }, []);
    x(() => {
      p();
    }, [p]), x(() => {
      e.sdk.api.fetch(e.app.apiUrl("/agents")).then((t) => t.json()).then((t) => d(t.agents || [])).catch(() => d([]));
    }, []);
    const y = i((t, l, n) => {
      c((b) => b.map((s) => s.id === t ? { ...s, [l]: l === "poll_interval_seconds" ? Number(n) || 0 : n } : s));
    }, []), _ = i(() => c((t) => [...t, h()]), []), w = i(async (t) => {
      const { _isNew: l, has_auth_token: n, ...b } = t;
      b.auth_token || delete b.auth_token;
      try {
        const s = await e.sdk.api.fetch(
          e.app.apiUrl(l ? "/cards" : `/cards/${encodeURIComponent(t.id)}`),
          {
            method: l ? "POST" : "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(b)
          }
        );
        if (!s.ok) {
          const C = await s.json().catch(() => ({}));
          v(C.detail || `Save failed (${s.status})`);
          return;
        }
        v("Saved"), await p();
      } catch (s) {
        v(s.message || "Save failed");
      }
    }, [p, v]), N = i(async (t) => {
      if (t._isNew) {
        c((l) => l.filter((n) => n.id !== t.id));
        return;
      }
      await e.sdk.api.fetch(e.app.apiUrl(`/cards/${encodeURIComponent(t.id)}`), { method: "DELETE" }), await p();
    }, [p]);
    return r === null ? /* @__PURE__ */ e.h("div", { className: "p-6 text-[var(--color-text-muted)]" }, "Loading...") : /* @__PURE__ */ e.h("div", { className: "p-6 max-w-3xl h-full overflow-y-auto" }, /* @__PURE__ */ e.h("div", { className: "flex items-center justify-between mb-4" }, /* @__PURE__ */ e.h("div", null, /* @__PURE__ */ e.h("h2", { className: "text-lg font-semibold text-[var(--color-text-primary)]" }, "Feed Subscriber"), /* @__PURE__ */ e.h("p", { className: "text-xs text-[var(--color-text-muted)] mt-1" }, "Each card polls a JSON source URL on its own interval. When the response changes since the last poll, it fires a Webhook Call or an Agent Call with the new entry.")), /* @__PURE__ */ e.h("div", { className: "flex items-center gap-3" }, u && /* @__PURE__ */ e.h("span", { className: "text-xs text-[var(--color-text-muted)]" }, u), /* @__PURE__ */ e.h(
      "button",
      {
        onClick: _,
        className: "px-3 py-1.5 text-xs rounded border border-[var(--color-border)] text-[var(--color-text-primary)] hover:bg-[var(--color-bg-secondary)] transition-colors"
      },
      "+ Add feed"
    ))), r.length === 0 && /* @__PURE__ */ e.h("div", { className: "text-xs text-[var(--color-text-muted)] italic" }, 'No feed subscriptions yet — click "+ Add feed" to create one.'), /* @__PURE__ */ e.h("div", { className: "space-y-4" }, r.map((t) => /* @__PURE__ */ e.h(
      k,
      {
        key: t.id,
        card: t,
        agents: o,
        onChange: (l, n) => y(t.id, l, n),
        onSave: () => w(t),
        onRemove: () => N(t)
      }
    ))));
  }
  function k({ card: r, agents: c, onChange: o, onSave: d, onRemove: u }) {
    return /* @__PURE__ */ e.h("div", { className: "bg-[var(--color-bg-secondary)] rounded-lg border border-[var(--color-border)] p-4 space-y-3" }, /* @__PURE__ */ e.h("div", { className: "flex items-center justify-between" }, /* @__PURE__ */ e.h(
      "input",
      {
        value: r.name,
        onChange: (a) => o("name", a.target.value),
        placeholder: "Feed name",
        className: "text-sm font-medium bg-transparent text-[var(--color-text-primary)] border-b border-transparent hover:border-[var(--color-border)] focus:border-[var(--color-accent)] outline-none px-1"
      }
    ), /* @__PURE__ */ e.h("div", { className: "flex items-center gap-3" }, /* @__PURE__ */ e.h(
      "button",
      {
        onClick: d,
        className: "text-xs px-2 py-1 rounded bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-hover)] transition-colors"
      },
      "Save"
    ), /* @__PURE__ */ e.h(
      "button",
      {
        onClick: u,
        className: "text-xs text-[var(--color-text-muted)] hover:text-red-400 transition-colors"
      },
      "Remove"
    ))), /* @__PURE__ */ e.h(
      m,
      {
        label: "Source JSON URL",
        value: r.source_url,
        onChange: (a) => o("source_url", a),
        placeholder: "https://example.com/feed.json"
      }
    ), /* @__PURE__ */ e.h(
      m,
      {
        label: "Poll Interval (seconds)",
        value: r.poll_interval_seconds,
        onChange: (a) => o("poll_interval_seconds", a),
        type: "number",
        placeholder: "60"
      }
    ), /* @__PURE__ */ e.h("label", { className: "block" }, /* @__PURE__ */ e.h("span", { className: "text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]" }, "Call type"), /* @__PURE__ */ e.h(
      "select",
      {
        value: r.call_type,
        onChange: (a) => o("call_type", a.target.value),
        className: "mt-0.5 w-full bg-[var(--color-bg-primary)] text-sm text-[var(--color-text-primary)] border border-[var(--color-border)] rounded px-2 py-1.5 outline-none focus:border-[var(--color-accent)] transition-colors"
      },
      /* @__PURE__ */ e.h("option", { value: "webhook" }, "Webhook Call"),
      /* @__PURE__ */ e.h("option", { value: "agent" }, "Agent Call")
    )), r.call_type === "agent" ? /* @__PURE__ */ e.h(e.React.Fragment, null, /* @__PURE__ */ e.h("label", { className: "block" }, /* @__PURE__ */ e.h("span", { className: "text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]" }, "Agent"), /* @__PURE__ */ e.h(
      "select",
      {
        value: r.agent_slug,
        onChange: (a) => o("agent_slug", a.target.value),
        className: "mt-0.5 w-full bg-[var(--color-bg-primary)] text-sm text-[var(--color-text-primary)] border border-[var(--color-border)] rounded px-2 py-1.5 outline-none focus:border-[var(--color-accent)] transition-colors"
      },
      /* @__PURE__ */ e.h("option", { value: "" }, "Select an agent…"),
      c.map((a) => /* @__PURE__ */ e.h("option", { key: a.slug, value: a.slug }, a.name || a.slug))
    )), /* @__PURE__ */ e.h(
      m,
      {
        label: "Target slug",
        value: r.target_slug,
        onChange: (a) => o("target_slug", a),
        placeholder: "w-feed-subscriber"
      }
    )) : /* @__PURE__ */ e.h(e.React.Fragment, null, /* @__PURE__ */ e.h(
      m,
      {
        label: "Target Webhook URL",
        value: r.webhook_url,
        onChange: (a) => o("webhook_url", a),
        placeholder: "https://agents-platform.internal/api/webhooks/..."
      }
    ), /* @__PURE__ */ e.h(
      m,
      {
        label: r.has_auth_token ? "Auth Token (Bearer) — set, leave blank to keep" : "Auth Token (Bearer)",
        value: r.auth_token,
        onChange: (a) => o("auth_token", a),
        type: "password",
        placeholder: "token used as Authorization: Bearer <token>"
      }
    )));
  }
  function m({ label: r, value: c, onChange: o, type: d = "text", placeholder: u }) {
    return /* @__PURE__ */ e.h("label", { className: "block" }, /* @__PURE__ */ e.h("span", { className: "text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]" }, r), /* @__PURE__ */ e.h(
      "input",
      {
        type: d,
        value: c,
        onChange: (a) => o(a.target.value),
        placeholder: u,
        className: "mt-0.5 w-full bg-[var(--color-bg-primary)] text-sm text-[var(--color-text-primary)] border border-[var(--color-border)] rounded px-2 py-1.5 outline-none focus:border-[var(--color-accent)] transition-colors"
      }
    ));
  }
  e.registerWindow("feed-subscriber.main", f);
}
export {
  S as default,
  S as register
};
