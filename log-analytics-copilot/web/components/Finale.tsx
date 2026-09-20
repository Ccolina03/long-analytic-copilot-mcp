"use client";

import { agentColor, agentName, agentShort } from "@/lib/types";
import { consultedFrom, skippedFrom } from "@/lib/sim";
import type { TraceEvent } from "@/lib/types";

type Props = {
  visible: boolean;
  doc: string | null;
  events: TraceEvent[];
  title: string;
  onClose: () => void;
  onReplay: () => void;
};

function scoreline(events: TraceEvent[]) {
  const consults = consultedFrom(events).length;
  const skips = skippedFrom(events).length;
  const rounds = new Set(
    events
      .filter((e) => typeof e.round_number === "number" && e.round_number > 0)
      .map((e) => e.round_number),
  ).size;
  const withConf = [...events]
    .reverse()
    .find((e) => typeof e.data?.confidence === "number");
  const raw = withConf ? Number(withConf.data.confidence) : null;
  const conf =
    raw == null ? null : `${Math.round(raw <= 1 ? raw * 100 : raw)}%`;
  return { consults, skips, rounds, conf };
}

export default function Finale({
  visible,
  doc,
  events,
  title,
  onClose,
  onReplay,
}: Props) {
  if (!visible) return null;
  const consults = consultedFrom(events);
  const skips = skippedFrom(events);
  const scores = scoreline(events);
  const preview = (doc || "")
    .split("\n")
    .filter((l) => l.trim())
    .slice(0, 18)
    .join("\n");

  return (
    <div className="finale" role="dialog" aria-label="Design 1-pager">
      <div className="finale-panel">
        <header className="finale-head">
          <div>
            <p className="finale-kicker">Converged · design 1-pager</p>
            <h2 className="finale-title">{title}</h2>
          </div>
          <div className="finale-actions">
            <button className="btn ghost" onClick={onReplay}>
              Replay
            </button>
            <button className="btn" onClick={onClose}>
              Back to mesh
            </button>
          </div>
        </header>

        <div className="finale-scores">
          <div>
            <strong>{scores.consults}</strong>
            <span>consulted</span>
          </div>
          <div>
            <strong>{scores.skips}</strong>
            <span>skipped</span>
          </div>
          <div>
            <strong>{scores.rounds || "—"}</strong>
            <span>rounds</span>
          </div>
          <div>
            <strong>{scores.conf || "—"}</strong>
            <span>confidence</span>
          </div>
        </div>

        <div className="finale-grid">
          <section className="finale-doc">
            <h3>Decision brief</h3>
            <pre>{preview || "1-pager rendering…"}</pre>
          </section>
          <aside className="finale-side">
            <section>
              <h3>Wired in</h3>
              {consults.map((e) => (
                <div className="finale-row" key={`c-${e.seq}`}>
                  <span className="badge" style={{ background: agentColor(e.to_agent) }}>
                    {agentShort(e.to_agent)}
                  </span>
                  <div>
                    <div className="name">{agentName(e.to_agent)}</div>
                    <div className="why">{e.detail}</div>
                  </div>
                </div>
              ))}
            </section>
            <section>
              <h3>Skipped</h3>
              {skips.map((e) => (
                <div className="finale-row dim" key={`s-${e.seq}`}>
                  <span className="badge" style={{ background: agentColor(e.to_agent) }}>
                    {agentShort(e.to_agent)}
                  </span>
                  <div>
                    <div className="name">{agentName(e.to_agent)}</div>
                    <div className="why">{e.detail}</div>
                  </div>
                </div>
              ))}
            </section>
          </aside>
        </div>
      </div>
    </div>
  );
}
