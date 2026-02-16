"use client";

import React, { useEffect, useMemo, useState } from "react";
import { z } from "zod";

const ChatResponse = z.object({
  session_id: z.string(),
  trace_id: z.string(),
  agent_state: z.string(),
  assistant_message: z.string(),
  recommended_offers: z.array(z.any()),
  tool_timeline: z.array(z.any()),
  guardrails: z.object({ tool_calls: z.number(), wall_clock_ms: z.number() })
});

const SessionListResponse = z.object({
  sessions: z.array(z.object({ session_id: z.string(), updated_at: z.string() }))
});

const SessionDetailResponse = z.object({
  session_id: z.string(),
  updated_at: z.string(),
  agent_state: z.string(),
  constraints: z.any(),
  snapshot: z.any()
});

const agentBaseUrl = process.env.NEXT_PUBLIC_AGENT_BASE_URL || "http://localhost:8000";

type Turn = {
  role: "user" | "assistant";
  content: string;
  trace_id?: string;
};

function jaegerTraceUrl(traceId: string) {
  return `http://localhost:16686/trace/${traceId}`;
}

export default function Page() {
  const [sessions, setSessions] = useState<Array<{ session_id: string; updated_at: string }>>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionDetail, setSessionDetail] = useState<any>(null);
  const [adminToken, setAdminToken] = useState(process.env.ADMIN_TOKEN || "dev-admin");
  const [filter, setFilter] = useState("");

  async function refreshSessions() {
    const r = await fetch(`${agentBaseUrl}/sessions`);
    const data = SessionListResponse.parse(await r.json());
    setSessions(data.sessions);
  }

  async function loadSession(id: string) {
    const r = await fetch(`${agentBaseUrl}/sessions/${id}`);
    const data = SessionDetailResponse.parse(await r.json());
    setSessionId(data.session_id);
    setSessionDetail(data);
    const history = (data.snapshot?.turns || []) as any[];
    if (history.length) {
      const mapped: Turn[] = [];
      for (const t of history) {
        if (t.user_message) mapped.push({ role: "user", content: String(t.user_message) });
        if (t.assistant_message) mapped.push({ role: "assistant", content: String(t.assistant_message), trace_id: t.trace_id });
      }
      setTurns(mapped);
    } else {
      const lastAssistant = data.snapshot?.assistant_message;
      setTurns(lastAssistant ? [{ role: "assistant", content: String(lastAssistant) }] : []);
    }
  }

  async function sendChat(text: string) {
    setLoading(true);
    try {
      setTurns((t) => [...t, { role: "user", content: text }]);
      const r = await fetch(`${agentBaseUrl}/chat`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, user_id: "web-user", message: text })
      });
      const data = ChatResponse.parse(await r.json());
      setSessionId(data.session_id);
      setTurns((t) => [...t, { role: "assistant", content: data.assistant_message, trace_id: data.trace_id }]);
      await refreshSessions();
      await loadSession(data.session_id);
    } finally {
      setLoading(false);
    }
  }

  async function selectOffer(offerId: string) {
    await sendChat(`I choose ${offerId}`);
  }

  async function adminSeed() {
    await fetch(`${agentBaseUrl}/admin/seed`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-admin-token": adminToken },
      body: JSON.stringify({ seed: 1337, hotels: 220, offers: 2600 })
    });
    await refreshSessions();
  }

  async function adminClearSessions() {
    await fetch(`${agentBaseUrl}/admin/clear_sessions`, {
      method: "POST",
      headers: { "x-admin-token": adminToken }
    });
    setSessionId(null);
    setTurns([]);
    setSessionDetail(null);
    await refreshSessions();
  }

  async function exportSession() {
    if (!sessionId || !sessionDetail) return;
    const blob = new Blob([JSON.stringify(sessionDetail, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `session_${sessionId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function importSession(file: File) {
    const text = await file.text();
    const parsed = JSON.parse(text);
    const id = parsed.session_id;
    await fetch(`${agentBaseUrl}/sessions/import`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-admin-token": adminToken },
      body: JSON.stringify({
        session_id: parsed.session_id,
        user_id: "imported",
        agent_state: parsed.agent_state || "WAIT_FOR_SELECTION",
        constraints: parsed.constraints || {},
        snapshot: parsed.snapshot || {}
      })
    });
    await refreshSessions();
    if (id) await loadSession(id);
  }

  useEffect(() => {
    refreshSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filteredSessions = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!f) return sessions;
    return sessions.filter((s) => s.session_id.toLowerCase().includes(f));
  }, [sessions, filter]);

  const turnHistory: any[] = sessionDetail?.snapshot?.turns || [];
  const latestTurn: any | null = turnHistory.length ? turnHistory[turnHistory.length - 1] : null;
  const lastOffersTurn: any | null =
    turnHistory.slice().reverse().find((t) => (t.recommended_offers || []).length) || null;

  const offers: any[] = lastOffersTurn?.recommended_offers || sessionDetail?.snapshot?.recommended_offers || [];
  const toolTimeline: any[] = latestTurn?.tool_timeline || sessionDetail?.snapshot?.tool_timeline || [];
  const recentTraces: string[] = sessionDetail?.snapshot?.recent_trace_ids || [];

  return (
    <div className="app">
      <div className="panel">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div>
            <div style={{ fontWeight: 700 }}>Sessions</div>
            <div className="muted">Click to resume</div>
          </div>
          <button className="btn" onClick={() => refreshSessions()}>Refresh</button>
        </div>

        <div style={{ marginTop: 10 }}>
          <input placeholder="Filter by session_id" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>

        <div style={{ marginTop: 10 }}>
          {filteredSessions.map((s) => (
            <div key={s.session_id} className="card" style={{ cursor: "pointer" }} onClick={() => loadSession(s.session_id)}>
              <div style={{ fontFamily: "monospace", fontSize: 12 }}>{s.session_id}</div>
              <div className="muted">{s.updated_at}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="panel">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div>
            <div style={{ fontWeight: 700 }}>Chat</div>
            <div className="muted">
              session_id: <span style={{ fontFamily: "monospace" }}>{sessionId || "(new)"}</span>
            </div>
          </div>
          <div className="row">
            <span className="pill">{latestTurn?.agent_state || sessionDetail?.agent_state || "UNKNOWN"}</span>
            <button className="btn" onClick={() => { setSessionId(null); setTurns([]); setSessionDetail(null); }}>
              New
            </button>
          </div>
        </div>

        <div style={{ marginTop: 10 }}>
          {turns.map((t, idx) => (
            <div key={idx} className="card">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div style={{ fontWeight: 700 }}>{t.role}</div>
                {t.trace_id ? (
                  <a href={jaegerTraceUrl(t.trace_id)} target="_blank" rel="noreferrer" className="muted">
                    trace {t.trace_id.slice(0, 8)}…
                  </a>
                ) : null}
              </div>
              <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>{t.content}</pre>
            </div>
          ))}
        </div>

        <div style={{ position: "sticky", bottom: 0, background: "#0b0f14", paddingTop: 10 }}>
          <textarea value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Type a message..." />
          <div className="row" style={{ justifyContent: "space-between", marginTop: 8 }}>
            <button
              className="btn btnPrimary"
              disabled={loading || !message.trim()}
              onClick={() => { const m = message.trim(); setMessage(""); sendChat(m); }}
            >
              {loading ? "Sending..." : "Send"}
            </button>
            <div className="muted">
              {sessionDetail?.snapshot?.recent_trace_ids?.length ? `traces: ${sessionDetail.snapshot.recent_trace_ids.length}` : ""}
            </div>
          </div>
        </div>
      </div>

      <div className="panelRight">
        <div style={{ fontWeight: 700 }}>Debug</div>
        <div className="card">
          <div className="muted">Constraints</div>
          <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(sessionDetail?.constraints || {}, null, 2)}</pre>
        </div>

        <div className="card">
          <div className="muted">Recent Traces</div>
          {recentTraces.map((t) => (
            <div key={t} style={{ fontFamily: "monospace", fontSize: 12 }}>
              <a href={jaegerTraceUrl(t)} target="_blank" rel="noreferrer">{t}</a>
            </div>
          ))}
        </div>

        <div style={{ fontWeight: 700, marginTop: 10 }}>Tool Timeline</div>
        <div className="muted">Latest turn only</div>
        {toolTimeline.map((e, idx) => (
          <div key={idx} className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <div style={{ fontFamily: "monospace" }}>{e.tool_name}</div>
              <span className="pill">{e.status}</span>
            </div>
            <div className="muted">
              latency_ms={e.latency_ms} retries={e.retries}
              {e.path ? ` path=${e.path}` : ""}
            </div>
            <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(e.result_counts || {}, null, 2)}</pre>
            <details style={{ marginTop: 8 }}>
              <summary className="muted" style={{ cursor: "pointer" }}>Details</summary>
              <div className="muted" style={{ marginTop: 6 }}>request payload</div>
              <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(e.payload || {}, null, 2)}</pre>
              <div className="muted">result preview</div>
              <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(e.result_preview || {}, null, 2)}</pre>
              <div className="muted">response keys</div>
              <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(e.response_keys || [], null, 2)}</pre>
              {e.url ? (
                <>
                  <div className="muted">url</div>
                  <pre style={{ whiteSpace: "pre-wrap" }}>{String(e.url)}</pre>
                </>
              ) : null}
            </details>
          </div>
        ))}

        <div style={{ fontWeight: 700, marginTop: 10 }}>Offers</div>
        {offers.map((o) => (
          <div key={o.offer_id} className="card">
            <div style={{ fontWeight: 700 }}>{o.hotel_name}</div>
            <div className="muted" style={{ fontFamily: "monospace" }}>
              offer_id={o.offer_id}
              {"\n"}hotel_id={o.hotel_id}
            </div>
            <div className="row" style={{ justifyContent: "space-between", marginTop: 6 }}>
              <span className="pill">${Number(o.total_price).toFixed(2)}</span>
              {o.star_rating != null ? <span className="pill">{String(o.star_rating)}★</span> : null}
              <span className="pill">{o.refundable ? "Refundable" : "Non-refundable"}</span>
              <span className="pill">{o.inventory_status}</span>
            </div>
            <div className="muted" style={{ marginTop: 6 }}>
              last_priced_ts={o.last_priced_ts} expires_ts={o.expires_ts}
            </div>
            <div className="muted">
              cancellation_deadline={o.cancellation_deadline || "(none)"}
            </div>
            <div className="row" style={{ marginTop: 8, justifyContent: "space-between" }}>
              <button className="btn btnPrimary" onClick={() => selectOffer(o.offer_id)}>
                Select
              </button>
              <a href={jaegerTraceUrl(turns[turns.length - 1]?.trace_id || "")} target="_blank" rel="noreferrer" className="muted">
                Open last trace
              </a>
            </div>
          </div>
        ))}

        <div style={{ fontWeight: 700, marginTop: 10 }}>Export / Import</div>
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <button className="btn" disabled={!sessionId} onClick={() => exportSession()}>
              Export session
            </button>
            <label className="btn">
              Import
              <input
                type="file"
                accept="application/json"
                style={{ display: "none" }}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) importSession(f);
                }}
              />
            </label>
          </div>
        </div>

        <div style={{ fontWeight: 700, marginTop: 10 }}>Admin (dev)</div>
        <div className="card">
          <div className="muted">x-admin-token</div>
          <input value={adminToken} onChange={(e) => setAdminToken(e.target.value)} />
          <div className="row" style={{ justifyContent: "space-between", marginTop: 8 }}>
            <button className="btn" onClick={() => adminSeed()}>Seed DB</button>
            <button className="btn" onClick={() => adminClearSessions()}>Clear Sessions</button>
          </div>
          <div className="muted" style={{ marginTop: 8 }}>
            These endpoints are token-gated and intended for local dev only.
          </div>
        </div>
      </div>
    </div>
  );
}

