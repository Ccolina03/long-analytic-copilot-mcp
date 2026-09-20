"use client";

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { type LaneState, type Packet } from "@/lib/sim";
import {
  LIVE_AGENTS,
  PHASE_COPY,
  agentColor,
  agentName,
  agentShort,
} from "@/lib/types";

type Pt = { x: number; y: number };

type Props = {
  lanes: LaneState[];
  packet: Packet | null;
  actorId: string;
  links: Array<[string, string]>;
  phase: string;
  now: string;
  thought: string;
  selected: string | null;
  onSelect: (id: string) => void;
};

/** Fixed seats — agents never move. Pentagon around a status core. */
const SEATS: Record<string, Pt> = {
  mirrormaker: { x: 50, y: 13 },
  "group-coordinator": { x: 86, y: 36 },
  "kafka-broker": { x: 74, y: 70 },
  "kafka-clients": { x: 26, y: 70 },
  "kafka-security": { x: 14, y: 36 },
};

function seat(id: string): Pt {
  return SEATS[id] ?? { x: 50, y: 50 };
}

function px(p: Pt, w: number, h: number): Pt {
  return { x: (p.x / 100) * w, y: (p.y / 100) * h };
}

/** Soft arc through the mesh interior so traffic reads as network flow. */
function curve(a: Pt, b: Pt, w: number, h: number): string {
  const A = px(a, w, h);
  const B = px(b, w, h);
  const mx = (A.x + B.x) / 2;
  const my = (A.y + B.y) / 2;
  const cx = mx + (w * 0.5 - mx) * 0.42;
  const cy = my + (h * 0.5 - my) * 0.42;
  return `M ${A.x.toFixed(1)} ${A.y.toFixed(1)} Q ${cx.toFixed(1)} ${cy.toFixed(1)} ${B.x.toFixed(1)} ${B.y.toFixed(1)}`;
}

const PAIRS: Array<[string, string]> = [];
for (let i = 0; i < LIVE_AGENTS.length; i += 1) {
  for (let j = i + 1; j < LIVE_AGENTS.length; j += 1) {
    PAIRS.push([LIVE_AGENTS[i], LIVE_AGENTS[j]]);
  }
}

function ActivityBars({ active }: { active: boolean }) {
  return (
    <div className={`bars ${active ? "on" : ""}`} aria-hidden>
      {[0, 1, 2, 3, 4].map((i) => (
        <span key={i} style={{ animationDelay: `${i * 0.12}s` }} />
      ))}
    </div>
  );
}

export default function Floor({
  lanes,
  packet,
  actorId,
  links,
  phase,
  now,
  thought,
  selected,
  onSelect,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ w: 1200, h: 700 });

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const apply = () => {
      const r = el.getBoundingClientRect();
      if (r.width && r.height) setBox({ w: r.width, h: r.height });
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const live = new Set(links.map(([a, b]) => [a, b].sort().join("|")));
  const hopKey = packet ? [packet.from, packet.to].sort().join("|") : "";
  const phaseLabel = PHASE_COPY[phase]?.label ?? (phase || "Standby");
  const actorLane = lanes.find((l) => l.id === actorId);

  const hopPath = useMemo(() => {
    if (!packet) return "";
    return curve(seat(packet.from), seat(packet.to), box.w, box.h);
  }, [packet, box.w, box.h]);

  return (
    <div className="sky" ref={ref}>
      <div className="sky-wash" aria-hidden />
      <div className="sky-vignette" aria-hidden />

      <svg className="mesh" viewBox={`0 0 ${box.w} ${box.h}`} preserveAspectRatio="none">
        <defs>
          <filter id="glow" x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur stdDeviation="3" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {LIVE_AGENTS.map((id) => {
          const p = px(seat(id), box.w, box.h);
          const lane = lanes.find((l) => l.id === id);
          const hot =
            lane &&
            (lane.mood === "thinking" ||
              lane.mood === "asking" ||
              lane.mood === "replying" ||
              lane.mood === "reading");
          return (
            <g key={`anchor-${id}`}>
              <circle
                cx={p.x}
                cy={p.y}
                r={hot ? 7 : 4}
                fill={hot ? agentColor(id) : "rgba(120,150,160,0.25)"}
                opacity={hot ? 0.55 : 0.35}
                className={hot ? "anchor-hot" : "anchor"}
              />
            </g>
          );
        })}

        {PAIRS.map(([a, b]) => {
          const key = [a, b].sort().join("|");
          const lit = live.has(key);
          const hop = hopKey === key;
          const d = curve(seat(a), seat(b), box.w, box.h);
          return (
            <path
              key={key}
              d={d}
              className={`link ${lit ? "lit" : ""} ${hop ? "hop" : ""}`}
              stroke={
                hop && packet
                  ? packet.color
                  : lit
                    ? "rgba(30, 200, 191, 0.55)"
                    : "rgba(120, 150, 160, 0.14)"
              }
            />
          );
        })}

        {packet && hopPath ? (
          <path
            key={`beam-${packet.id}`}
            d={hopPath}
            className={`beam ${packet.kind}`}
            stroke={packet.color}
            filter="url(#glow)"
          />
        ) : null}
      </svg>

      <div className={`hud ${actorLane?.mood ?? "idle"}`}>
        <div className="hud-phase">
          <span className="hud-dot" />
          {phaseLabel}
        </div>
        <div className="hud-now">{now || "Mesh idle"}</div>
        {thought ? <p className="hud-thought">{thought}</p> : null}
        {actorLane && actorLane.mood !== "idle" ? (
          <div className="hud-actor" style={{ ["--agent" as string]: agentColor(actorId) }}>
            <span className="hud-chip">{agentShort(actorId)}</span>
            <span>{actorLane.processing}</span>
          </div>
        ) : null}
      </div>

      {lanes.map((lane) => {
        const p = seat(lane.id);
        const liveNode =
          lane.mood === "thinking" ||
          lane.mood === "asking" ||
          lane.mood === "replying" ||
          lane.mood === "reading";
        const isActor = lane.id === actorId && liveNode;
        return (
          <article
            key={lane.id}
            className={[
              "pod",
              lane.mood,
              selected === lane.id ? "on" : "",
              isActor ? "actor" : "",
              lane.consulted ? "" : "dim",
            ].join(" ")}
            style={{
              left: `${p.x}%`,
              top: `${p.y}%`,
              ["--agent" as string]: agentColor(lane.id),
            }}
            onClick={() => onSelect(lane.id)}
          >
            <header className="pod-head">
              <div className="pod-mark">
                {liveNode ? <span className="pod-ring" /> : null}
                {agentShort(lane.id)}
              </div>
              <div className="pod-id">
                <div className="pod-name">{agentName(lane.id)}</div>
                <div className={`pod-status ${lane.mood}`}>
                  <i />
                  {lane.status}
                </div>
              </div>
              {liveNode ? <ActivityBars active /> : <span className="pod-idle-slot" />}
            </header>
            <div className="pod-body">
              <div className="pod-task">{lane.processing}</div>
              {liveNode && lane.thought ? (
                <p className="pod-thought">{lane.thought}</p>
              ) : null}
            </div>
          </article>
        );
      })}

      {packet && hopPath ? (
        <div
          key={packet.id}
          className={`signal ${packet.kind}`}
          style={{
            offsetPath: `path("${hopPath}")`,
            ["--ink" as string]: packet.color,
          }}
        >
          <span className="signal-route">{packet.kicker}</span>
          <span className="signal-body">{packet.blurb}</span>
        </div>
      ) : null}
    </div>
  );
}
