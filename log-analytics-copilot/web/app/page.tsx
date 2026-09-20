"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  PHASES,
  PHASE_COPY,
  agentColor,
  agentShort,
  type TicketSnapshot,
  type Trace,
  type TraceEvent,
} from "@/lib/types";
import {
  actingAgent,
  consultedFrom,
  currentHop,
  deriveLanes,
  interactionLog,
  meshLinks,
  nowCopy,
  skippedFrom,
  thoughtFrom,
} from "@/lib/sim";
import Floor from "@/components/Floor";
import Splash from "@/components/Splash";
import JiraTicket from "@/components/JiraTicket";
import Spotlight from "@/components/Spotlight";
import Finale from "@/components/Finale";
import BeatCaption, {
  BEAT_MS,
  beatForEvent,
  demoEvents,
  type Beat,
} from "@/components/BeatCaption";

type Tab = "event" | "doc";

async function startDemo(): Promise<string> {
  const resp = await fetch("/api/demo", { method: "POST" });
  if (!resp.ok) throw new Error(`API ${resp.status}`);
  return (await resp.json()).id as string;
}

async function waitForRun(id: string): Promise<TicketSnapshot> {
  for (let i = 0; i < 200; i += 1) {
    const resp = await fetch(`/api/tickets/${id}`);
    if (!resp.ok) throw new Error(`API ${resp.status}`);
    const snap = (await resp.json()) as TicketSnapshot;
    if (snap.status !== "running") return snap;
    await new Promise((r) => setTimeout(r, 50));
  }
  throw new Error("timed out waiting for the agent run");
}

/** ~3× faster than a normal watch — fits a ~80s explain+show cut. */
function delayFor(kind: string, demo: boolean): number {
  if (!demo) {
    if (kind === "request" || kind === "response") return 4200;
    if (kind === "peer_decision") return 2200;
    if (kind === "finding" || kind === "alternative" || kind === "concern") return 1200;
    if (kind === "tool_call") return 800;
    if (kind === "ticket") return 2800;
    return 220;
  }
  if (kind === "request" || kind === "response") return 2100;
  if (kind === "peer_decision") return 950;
  if (kind === "finding" || kind === "alternative") return 550;
  if (kind === "tool_call") return 360;
  if (kind === "ticket") return 2400;
  if (kind === "artifact" || kind === "convergence") return 700;
  return 150;
}

function replay(
  trace: Trace,
  onEvent: (e: TraceEvent) => void,
  onBeat: (beat: Beat | null) => void,
  onDone: () => void,
  demo: boolean,
) {
  const events = demo ? demoEvents(trace.events) : trace.events;
  let i = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const seen = new Set<string>();

  const clear = () => {
    if (timer) clearTimeout(timer);
  };

  const tick = () => {
    if (i >= events.length) {
      onBeat(null);
      onDone();
      return;
    }
    const e = events[i];
    const beat = demo ? beatForEvent(e, seen) : null;

    const advance = () => {
      onBeat(null);
      onEvent(e);
      i += 1;
      timer = setTimeout(tick, events[i] ? delayFor(e.kind, demo) : 80);
    };

    if (beat) {
      // Pause on the step banner so it can appear → hold → disappear on camera
      onBeat(beat);
      timer = setTimeout(advance, BEAT_MS);
    } else {
      advance();
    }
  };

  tick();
  return clear;
}

function jiraKeyFrom(url: string, fallback: string): string {
  const m = url.match(/\/browse\/([A-Z]+-\d+)/i);
  return m?.[1] ?? fallback;
}

export default function Page() {
  const [ready, setReady] = useState(false);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [doc, setDoc] = useState<string | null>(null);
  const [ticketMeta, setTicketMeta] = useState<{
    title: string;
    description: string;
    priority: string;
    team: string;
    source_url: string;
  } | null>(null);
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [selectedEvent, setSelectedEvent] = useState<number | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<string | null>("mirrormaker");
  const [tab, setTab] = useState<Tab>("event");
  const [error, setError] = useState<string | null>(null);
  const [left, setLeft] = useState(false);
  const [right, setRight] = useState(false);
  const [showJira, setShowJira] = useState(false);
  const [showFinale, setShowFinale] = useState(false);
  const [spotlightHold, setSpotlightHold] = useState(false);
  const [beat, setBeat] = useState<Beat | null>(null);
  const [demoMode, setDemoMode] = useState(false);
  const stopRef = useRef<(() => void) | null>(null);
  const jiraTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const spotTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autoDemo = useRef(false);
  const musicRef = useRef<HTMLAudioElement | null>(null);

  const stopMusic = () => {
    const a = musicRef.current;
    if (!a) return;
    a.pause();
    a.currentTime = 0;
  };

  const startMusic = () => {
    stopMusic();
    const a = new Audio("/demo-music.mp3");
    a.loop = false;
    a.volume = 0.55;
    musicRef.current = a;
    void a.play().catch(() => {
      /* autoplay may be blocked outside the demo click path */
    });
  };

  useEffect(() => {
    setReady(true);
    if (typeof window !== "undefined") {
      const q = new URLSearchParams(window.location.search);
      autoDemo.current = q.get("demo") === "1" || q.get("record") === "1";
    }
  }, []);

  const currentPhase = events.at(-1)?.phase ?? "";
  const reached = useMemo(() => new Set(events.map((e) => e.phase)), [events]);
  const lanes = useMemo(
    () => deriveLanes(events, status === "done"),
    [events, status],
  );
  const actorId = useMemo(() => actingAgent(events), [events]);
  const packet = useMemo(() => currentHop(events), [events]);
  const links = useMemo(() => meshLinks(events), [events]);
  const happening = useMemo(() => nowCopy(events), [events]);
  const coreThought = useMemo(() => {
    const e = events.at(-1);
    return e ? thoughtFrom(e) : "";
  }, [events]);
  const traffic = useMemo(() => interactionLog(events), [events]);
  const skips = skippedFrom(events);
  const consults = consultedFrom(events);
  const liveSeq = traffic.at(-1)?.seq;
  const activeSeq = selectedEvent ?? liveSeq;
  const sel = events.find((e) => e.seq === activeSeq) ?? events.at(-1);
  const ticket = events.find((e) => e.kind === "ticket");

  const discoveryItems = useMemo(() => {
    return events
      .filter((e) => e.kind === "peer_decision" && e.to_agent)
      .map((e) => ({
        seq: e.seq,
        to: e.to_agent,
        consult: Boolean(e.data.consult),
        reason: e.detail || e.title,
      }));
  }, [events]);

  const discoveryFrom = useMemo(() => {
    const first = events.find((e) => e.kind === "peer_decision");
    return first?.agent || "mirrormaker";
  }, [events]);

  const showSpotlight =
    spotlightHold ||
    (status === "running" &&
      currentPhase === "discovery" &&
      discoveryItems.length > 0 &&
      !showJira);

  const pushEvent = (e: TraceEvent) => {
    setEvents((prev) => [...prev, e]);
    if (e.agent) setSelectedAgent(e.agent);
    if (e.kind === "ticket") {
      setShowJira(true);
      if (jiraTimer.current) clearTimeout(jiraTimer.current);
      jiraTimer.current = setTimeout(() => setShowJira(false), 2000);
    }
    if (e.kind === "peer_decision") {
      setSpotlightHold(true);
      if (spotTimer.current) clearTimeout(spotTimer.current);
      spotTimer.current = setTimeout(() => setSpotlightHold(false), 1600);
    }
  };

  const reset = () => {
    stopRef.current?.();
    stopMusic();
    if (jiraTimer.current) clearTimeout(jiraTimer.current);
    if (spotTimer.current) clearTimeout(spotTimer.current);
    setEvents([]);
    setDoc(null);
    setTicketMeta(null);
    setSelectedEvent(null);
    setError(null);
    setTab("event");
    setShowJira(false);
    setShowFinale(false);
    setSpotlightHold(false);
    setBeat(null);
    setDemoMode(false);
  };

  const runLive = async () => {
    reset();
    setStatus("running");
    setDemoMode(false);
    try {
      const id = await startDemo();
      const snap = await waitForRun(id);
      if (snap.status === "error") {
        setError(snap.error ?? "run failed");
        setStatus("error");
        return;
      }
      setTicketMeta({
        title: snap.ticket.title,
        description: snap.ticket.description,
        priority: snap.ticket.priority,
        team: snap.ticket.team,
        source_url: snap.ticket.source_url,
      });
      const trace: Trace = snap.trace ?? {
        ticket_id: id,
        title: snap.ticket.title,
        phases: [],
        phases_reached: [],
        agents: [],
        event_count: snap.events.length,
        duration_ms: snap.events.at(-1)?.t_ms ?? 1,
        events: snap.events,
      };
      stopRef.current = replay(
        trace,
        pushEvent,
        setBeat,
        () => {
          setDoc(snap.doc);
          setStatus("done");
          setShowFinale(true);
        },
        false,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "API unreachable");
      setStatus("error");
    }
  };

  const runRecorded = async (demo = true) => {
    reset();
    setStatus("running");
    setDemoMode(demo);
    if (demo) startMusic();
    const trace = (await (await fetch("/demo-trace.json")).json()) as Trace & {
      doc?: string;
      ticket?: {
        title: string;
        description: string;
        priority: string;
        team: string;
        source_url: string;
      };
    };
    setTicketMeta({
      title: trace.title,
      description:
        trace.ticket?.description ||
        "Group discovery scans every consumer group on emit.checkpoints.interval — 8-12s p99 on clusters with >10k groups.",
      priority: trace.ticket?.priority || "high",
      team: trace.ticket?.team || "mirrormaker",
      source_url:
        trace.ticket?.source_url ||
        "https://issues.apache.org/jira/browse/KAFKA-18231",
    });
    stopRef.current = replay(
      trace,
      pushEvent,
      setBeat,
      () => {
        setDoc(trace.doc ?? null);
        setStatus("done");
        setShowFinale(true);
        // Soft fade-out over a few seconds once the 1-pager lands
        const a = musicRef.current;
        if (a) {
          const start = a.volume;
          const t0 = performance.now();
          const fade = (now: number) => {
            const t = Math.min(1, (now - t0) / 3500);
            a.volume = Math.max(0, start * (1 - t));
            if (t < 1) requestAnimationFrame(fade);
            else stopMusic();
          };
          requestAnimationFrame(fade);
        }
      },
      demo,
    );
  };

  useEffect(() => {
    if (!ready || !autoDemo.current) return;
    autoDemo.current = false;
    void runRecorded(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  if (!ready) return <div className="shell" />;

  if (status === "idle") {
    return (
      <div className="shell splash-shell">
        <Splash onStart={runLive} onReplay={() => runRecorded(true)} />
      </div>
    );
  }

  const title =
    ticketMeta?.title ||
    ticket?.detail ||
    "MirrorCheckpointConnector group discovery is slow";
  const jiraKey = jiraKeyFrom(ticketMeta?.source_url || "", "KAFKA-18231");

  return (
    <div className={`shell ${demoMode ? "demo" : ""}`}>
      <header className="top">
        <div>
          <div className="brand">
            SME <em>network</em>
          </div>
          <div className="tagline">
            Domain experts discover the blast radius, skip the rest, and land a
            decision brief.
          </div>
        </div>
        <div className="actions">
          <span className="live">
            {status === "running" && happening}
            {status === "done" && `Done · ${events.length} events`}
            {status === "error" && (error ?? "Error")}
          </span>
          <button className="btn" onClick={runLive} disabled={status === "running"}>
            Run live
          </button>
          <button
            className="btn ghost"
            onClick={() => runRecorded(true)}
            disabled={status === "running"}
          >
            Replay
          </button>
        </div>
      </header>

      <div className="workspace">
        <Floor
          lanes={lanes}
          packet={packet}
          actorId={actorId}
          links={links}
          phase={currentPhase}
          now={happening}
          thought={coreThought}
          selected={selectedAgent}
          onSelect={setSelectedAgent}
        />

        <BeatCaption beat={beat} />

        <JiraTicket
          visible={showJira}
          keyId={jiraKey}
          title={title}
          description={
            ticketMeta?.description ||
            ticket?.detail ||
            "Incoming Jira issue routed to the owning SME."
          }
          priority={ticketMeta?.priority || "high"}
          team={ticketMeta?.team || "mirrormaker"}
          sourceUrl={ticketMeta?.source_url}
        />

        <Spotlight
          visible={showSpotlight && !showJira && !showFinale}
          from={discoveryFrom}
          items={discoveryItems}
        />

        <Finale
          visible={showFinale}
          doc={doc}
          events={events}
          title={title}
          onClose={() => setShowFinale(false)}
          onReplay={() => runRecorded(true)}
        />

        {left && (
          <aside className="dock left">
            <header>
              Directory
              <button className="btn ghost" onClick={() => setLeft(false)}>
                Hide
              </button>
            </header>
            <div className="body">
              {consults.length === 0 && skips.length === 0 && (
                <p className="empty">
                  After investigation, this lists who was pulled in and who was skipped — with the reason.
                </p>
              )}
              {consults.map((e) => (
                <div className="drow" key={`c-${e.seq}`}>
                  <div className="badge" style={{ background: agentColor(e.to_agent) }}>
                    {agentShort(e.to_agent)}
                  </div>
                  <div>
                    <div className="name">{e.to_agent}</div>
                    <div className="why">{e.detail}</div>
                  </div>
                  <div className="pill">consult</div>
                </div>
              ))}
              {skips.map((e) => (
                <div className="drow" key={`s-${e.seq}`}>
                  <div className="badge" style={{ background: agentColor(e.to_agent) }}>
                    {agentShort(e.to_agent)}
                  </div>
                  <div>
                    <div className="name">{e.to_agent}</div>
                    <div className="why">{e.detail}</div>
                  </div>
                  <div className="pill skip">skip</div>
                </div>
              ))}
            </div>
          </aside>
        )}

        {right && (
          <aside className="dock right">
            <header>
              Traffic
              <button className="btn ghost" onClick={() => setRight(false)}>
                Hide
              </button>
            </header>
            <div className="tabs">
              <button className={tab === "event" ? "tab on" : "tab"} onClick={() => setTab("event")}>
                Log
              </button>
              <button
                className={tab === "doc" ? "tab on" : "tab"}
                onClick={() => {
                  setTab("doc");
                  if (doc) setShowFinale(true);
                }}
                disabled={!doc}
              >
                1-pager
              </button>
            </div>
            <div className="body">
              {tab === "doc" && doc && <div className="doc">{doc}</div>}
              {tab === "event" && traffic.length === 0 && (
                <p className="empty">Asks, replies, consults, and skips collect here as they happen.</p>
              )}
              {tab === "event" && traffic.length > 0 && (
                <div className="log">
                  {traffic.map((e) => {
                    const on = e.seq === activeSeq;
                    const verb =
                      e.kind === "request"
                        ? "ask"
                        : e.kind === "response"
                          ? "reply"
                          : e.data.consult
                            ? "consult"
                            : "skip";
                    return (
                      <button
                        key={e.seq}
                        className={`log-item ${on ? "on" : ""}`}
                        onClick={() => setSelectedEvent(e.seq)}
                      >
                        <div className="log-meta">
                          <span className="badge" style={{ background: agentColor(e.agent) }}>
                            {agentShort(e.agent)}
                          </span>
                          <span className="verb">{verb}</span>
                          {e.to_agent ? (
                            <span className="badge" style={{ background: agentColor(e.to_agent) }}>
                              {agentShort(e.to_agent)}
                            </span>
                          ) : null}
                        </div>
                        <div className="log-blurb">{thoughtFrom(e) || e.title}</div>
                      </button>
                    );
                  })}
                  {sel && (sel.kind === "request" || sel.kind === "response") && (
                    <div className="log-detail">
                      <div className="kind">
                        {sel.phase} · {sel.kind}
                        {sel.to_agent ? ` · ${sel.agent} → ${sel.to_agent}` : ""}
                      </div>
                      <h3 className="detail-title">{sel.title}</h3>
                      <div className="prose">{sel.detail || "—"}</div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </aside>
        )}
      </div>

      <div className="bar">
        <nav className="phases">
          {PHASES.map((p) => {
            const copy = PHASE_COPY[p];
            const cls = [
              "phase",
              reached.has(p) ? "on" : "",
              currentPhase === p ? "current" : "",
            ].join(" ");
            return (
              <div key={p} className={cls}>
                <span className="n">{copy.n}</span>
                <span className="lbl">{copy.label}</span>
              </div>
            );
          })}
        </nav>
        <div className="toggles">
          {status === "done" && (
            <button className="toggle on" onClick={() => setShowFinale(true)}>
              1-pager
            </button>
          )}
          <button className={`toggle ${left ? "on" : ""}`} onClick={() => setLeft((v) => !v)}>
            Directory
          </button>
          <button className={`toggle ${right ? "on" : ""}`} onClick={() => setRight((v) => !v)}>
            Traffic{traffic.length ? ` · ${traffic.length}` : ""}
          </button>
        </div>
      </div>
    </div>
  );
}
