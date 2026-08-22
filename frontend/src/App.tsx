import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  Activity,
  ArchiveRestore,
  ChevronRight,
  CircleStop,
  Command,
  Database,
  FolderGit2,
  History,
  Plus,
  RefreshCw,
  Search,
  Send,
  Trash2,
  Wifi,
  WifiOff,
} from "lucide-react";
import { ApprovalCard } from "./components/ApprovalCard";
import { NewSessionDialog } from "./components/NewSessionDialog";
import { StatusBadge } from "./components/StatusBadge";
import { Timeline } from "./components/Timeline";
import { api } from "./lib/api";
import type {
  ApprovalDecision,
  EventEnvelope,
  ProviderHealth,
  ProviderName,
  PublicConfig,
  Session,
  SessionDetail,
  SessionStatus,
} from "./types";

type Filter = "all" | ProviderName | "active";

function timeAgo(value: string) {
  const seconds = Math.max(1, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [providers, setProviders] = useState<ProviderHealth[]>([]);
  const [config, setConfig] = useState<PublicConfig>({ workspace_roots: [], history_storage: "" });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [connected, setConnected] = useState(false);
  const [newSessionOpen, setNewSessionOpen] = useState(false);
  const [composer, setComposer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectedRef = useRef<string | null>(null);

  const loadSessions = useCallback(async () => {
    const next = await api.sessions();
    setSessions(next);
    setSelectedId((current) => current ?? next[0]?.id ?? null);
  }, []);

  const loadDetail = useCallback(async (id: string) => {
    setDetail(await api.session(id));
  }, []);

  const reload = useCallback(async () => {
    try {
      const [nextProviders, nextConfig] = await Promise.all([api.providers(), api.config()]);
      setProviders(nextProviders);
      setConfig(nextConfig);
      await loadSessions();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to load Accretion");
    }
  }, [loadSessions]);

  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => {
    selectedRef.current = selectedId;
    if (selectedId) void loadDetail(selectedId);
    else setDetail(null);
  }, [loadDetail, selectedId]);

  useEffect(() => {
    let retry: number | undefined;
    let socket: WebSocket | undefined;
    let cancelled = false;
    function connect() {
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${window.location.host}/api/v1/events`);
      socket.onopen = () => setConnected(true);
      socket.onclose = () => {
        setConnected(false);
        if (!cancelled) retry = window.setTimeout(connect, 1500);
      };
      socket.onmessage = (message) => {
        const envelope = JSON.parse(message.data) as EventEnvelope;
        if (envelope.type === "snapshot") {
          setSessions((envelope.data.sessions as Session[]) ?? []);
          return;
        }
        void loadSessions();
        const eventSession = (envelope.data.session_id as string | undefined) ??
          ((envelope.data as unknown as Session).id as string | undefined);
        if (selectedRef.current && (!eventSession || eventSession === selectedRef.current)) {
          void loadDetail(selectedRef.current);
        }
      };
    }
    connect();
    return () => {
      cancelled = true;
      if (retry) window.clearTimeout(retry);
      socket?.close();
    };
  }, [loadDetail, loadSessions]);

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return sessions.filter((session) => {
      const matchesFilter = filter === "all" || session.provider === filter ||
        (filter === "active" && ["running", "waiting_approval"].includes(session.status));
      const matchesSearch = !query || session.title.toLowerCase().includes(query) ||
        session.cwd.toLowerCase().includes(query);
      return matchesFilter && matchesSearch;
    });
  }, [filter, search, sessions]);

  const stats = useMemo(() => ({
    active: sessions.filter((item) => ["running", "waiting_approval"].includes(item.status)).length,
    approvals: sessions.filter((item) => item.status === "waiting_approval").length,
    history: sessions.length,
  }), [sessions]);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await loadSessions();
      if (selectedRef.current) await loadDetail(selectedRef.current);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!detail || !composer.trim()) return;
    const prompt = composer.trim();
    setComposer("");
    await act(() => api.message(detail.id, prompt));
  }

  async function start(input: { provider: ProviderName; cwd: string; prompt: string }) {
    await act(async () => {
      const session = await api.start(input);
      setSelectedId(session.id);
    });
  }

  const pending = detail?.approvals.filter((item) => item.status === "pending") ?? [];

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand__mark"><span /></span>
          <div><strong>Accretion</strong><small>LOCAL CONTROL PLANE</small></div>
        </div>
        <div className="topbar__status">
          {providers.map((provider) => (
            <span className={`provider-health ${provider.available ? "is-online" : ""}`} key={provider.name} title={provider.version ?? provider.detail ?? ""}>
              <span />{provider.name === "codex" ? "Codex" : "Claude"}
            </span>
          ))}
          <span className={`connection ${connected ? "is-online" : ""}`}>
            {connected ? <Wifi size={14} /> : <WifiOff size={14} />}
            {connected ? "Live" : "Reconnecting"}
          </span>
        </div>
      </header>

      <aside className="sidebar">
        <button className="button button--primary new-session" onClick={() => setNewSessionOpen(true)}>
          <Plus size={17} /> New session
        </button>
        <div className="search-box"><Search size={15} /><input aria-label="Search sessions" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search sessions" /></div>
        <nav className="filter-row" aria-label="Session filters">
          {(["all", "active", "codex", "claude"] as Filter[]).map((item) => (
            <button className={filter === item ? "is-active" : ""} onClick={() => setFilter(item)} key={item}>{item}</button>
          ))}
        </nav>
        <div className="session-list">
          {filtered.map((session) => (
            <button className={`session-row ${selectedId === session.id ? "is-selected" : ""}`} onClick={() => setSelectedId(session.id)} key={session.id}>
              <span className={`provider-mark provider-mark--${session.provider}`}>{session.provider === "codex" ? "CX" : "CL"}</span>
              <span className="session-row__content">
                <strong>{session.title}</strong>
                <small><span className={`mini-dot mini-dot--${session.status}`} />{timeAgo(session.updated_at)} · {session.managed ? "managed" : "history"}</small>
              </span>
              <ChevronRight size={15} />
            </button>
          ))}
          {!filtered.length && <p className="sidebar-empty">No sessions match this view.</p>}
        </div>
        <div className="sidebar__footer">
          <button onClick={() => void act(() => api.importHistory())}><RefreshCw size={14} /> Sync history</button>
          <button onClick={() => void act(() => api.clear())}><Trash2 size={14} /> Clear completed</button>
        </div>
      </aside>

      <main className="main-panel">
        {!detail ? (
          <section className="welcome">
            <span className="orbit"><span /></span>
            <p className="eyebrow">Observable by default</p>
            <h1>One quiet place for every coding agent.</h1>
            <p>Start a managed run or select imported history to inspect its trajectory.</p>
            <button className="button button--primary" onClick={() => setNewSessionOpen(true)}><Plus size={17} /> Start session</button>
          </section>
        ) : (
          <>
            <section className="session-header">
              <div className="session-header__identity">
                <span className={`provider-mark provider-mark--${detail.provider}`}>{detail.provider === "codex" ? "CX" : "CL"}</span>
                <div>
                  <div className="session-header__title"><h1>{detail.title}</h1><StatusBadge status={detail.status} /></div>
                  <p><FolderGit2 size={14} /> {detail.cwd || "Unknown workspace"}</p>
                </div>
              </div>
              <div className="session-header__actions">
                {!detail.managed && <button className="button button--warm" disabled={busy || !detail.cwd} onClick={() => void act(() => api.resume(detail.id))}><ArchiveRestore size={16} /> Resume here</button>}
                {detail.managed && ["running", "waiting_approval"].includes(detail.status) && <button className="button button--danger-ghost" disabled={busy} onClick={() => void act(() => api.interrupt(detail.id))}><CircleStop size={16} /> Interrupt</button>}
                {!(["running", "waiting_approval"] as SessionStatus[]).includes(detail.status) && <button className="icon-button" disabled={busy} title="Delete session" onClick={() => void act(async () => { await api.remove(detail.id); setSelectedId(null); })}><Trash2 size={17} /></button>}
              </div>
            </section>

            <section className="metric-strip">
              <div><Activity size={16} /><span><small>STATE</small><strong>{detail.status.replace("_", " ")}</strong></span></div>
              <div><Command size={16} /><span><small>PROVIDER</small><strong>{detail.provider}</strong></span></div>
              <div><Database size={16} /><span><small>EVENTS</small><strong>{detail.events.length}</strong></span></div>
              <div><History size={16} /><span><small>UPDATED</small><strong>{timeAgo(detail.updated_at)}</strong></span></div>
            </section>

            {pending.map((approval) => <ApprovalCard approval={approval} key={approval.id} onDecision={(decision: ApprovalDecision) => act(() => api.decide(approval.id, decision))} />)}

            <section className="trajectory">
              <div className="section-heading"><div><p className="eyebrow">Trajectory</p><h2>Session timeline</h2></div><span>{detail.events.length} events</span></div>
              <Timeline events={detail.events} />
            </section>

            {detail.managed && (
              <form className="composer" onSubmit={send}>
                <textarea aria-label="Follow-up instruction" value={composer} onChange={(event) => setComposer(event.target.value)} placeholder={detail.status === "running" ? "Steer the active turn…" : "Continue this session…"} rows={2} />
                <button className="composer__send" disabled={busy || !composer.trim()} aria-label="Send instruction"><Send size={17} /></button>
                <span className="composer__hint">Shift + Enter for a new line</span>
              </form>
            )}
          </>
        )}
      </main>

      <aside className="inspector">
        <p className="eyebrow">System overview</p>
        <h2>Local orbit</h2>
        <div className="system-viz">
          <span className="system-viz__core">A</span>
          <span className="system-viz__orbit system-viz__orbit--one"><i>CX</i></span>
          <span className="system-viz__orbit system-viz__orbit--two"><i>CL</i></span>
        </div>
        <div className="stats-grid">
          <div><strong>{stats.active}</strong><span>Active</span></div>
          <div><strong>{stats.approvals}</strong><span>Waiting</span></div>
          <div><strong>{stats.history}</strong><span>Sessions</span></div>
        </div>
        <div className="privacy-note"><Database size={16} /><div><strong>Stored locally</strong><p>Transcripts and events stay in your local SQLite database.</p></div></div>
        <div className="roots"><small>WORKSPACE ROOTS</small>{config.workspace_roots.map((root) => <code key={root}>{root}</code>)}</div>
      </aside>

      {error && <div className="toast" role="alert"><span>{error}</span><button onClick={() => setError(null)}>Dismiss</button></div>}
      <NewSessionDialog open={newSessionOpen} providers={providers} roots={config.workspace_roots} onClose={() => setNewSessionOpen(false)} onSubmit={start} />
    </div>
  );
}

export default App;
