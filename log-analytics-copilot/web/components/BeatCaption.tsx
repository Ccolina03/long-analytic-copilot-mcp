"use client";

export type Beat = {
  id: string;
  step: string;
  label: string;
  title: string;
  line: string;
};

export const BEATS: Beat[] = [
  {
    id: "intake",
    step: "1",
    label: "Intake",
    title: "A Jira ticket lands",
    line: "Routed to the owning SME — no central orchestrator.",
  },
  {
    id: "investigate",
    step: "2",
    label: "Investigate",
    title: "Owner digs in",
    line: "MirrorMaker reads runbooks, forms findings, maps blast radius.",
  },
  {
    id: "discover",
    step: "3",
    label: "Discover",
    title: "Who enters the room",
    line: "Consult teams that own the impact. Skip everyone else.",
  },
  {
    id: "deliberate",
    step: "4",
    label: "Deliberate",
    title: "Peers talk on the mesh",
    line: "Asks and replies fly until the design holds.",
  },
  {
    id: "decide",
    step: "5",
    label: "Decide",
    title: "Land the 1-pager",
    line: "Decision brief: consulted, skipped, recommendation.",
  },
];

/** How long each step banner stays on screen (in + hold + out). */
export const BEAT_MS = 2800;

type Props = {
  beat: Beat | null;
};

/** Step transition — appears, holds, disappears over the live demo. */
export default function BeatCaption({ beat }: Props) {
  if (!beat) return null;
  return (
    <div className="beat-stage" key={beat.id} role="status" aria-live="polite">
      <div className="beat-banner">
        <div className="beat-banner-kicker">
          <span className="beat-banner-step">Step {beat.step}</span>
          <span className="beat-banner-label">{beat.label}</span>
        </div>
        <h2 className="beat-banner-title">{beat.title}</h2>
        <p className="beat-banner-line">{beat.line}</p>
        <div className="beat-banner-progress" aria-hidden />
      </div>
    </div>
  );
}

export function beatForEvent(
  e: { kind: string; phase: string },
  seen: Set<string>,
): Beat | null {
  const pick = (id: string) => {
    if (seen.has(id)) return null;
    const beat = BEATS.find((b) => b.id === id) ?? null;
    if (beat) seen.add(id);
    return beat;
  };

  if (e.kind === "ticket") return pick("intake");
  if (e.phase === "investigation" && (e.kind === "tool_call" || e.kind === "finding")) {
    return pick("investigate");
  }
  if (e.kind === "peer_decision") return pick("discover");
  if (e.kind === "request") return pick("deliberate");
  if (
    e.kind === "artifact" ||
    e.phase === "artifact" ||
    e.phase === "decision" ||
    e.phase === "synthesis"
  ) {
    return pick("decide");
  }
  return null;
}

/** Keep the beats that read on camera; drop noisy filler for the ~80s cut. */
export function demoEvents<T extends { kind: string }>(events: T[]): T[] {
  let alts = 0;
  return events.filter((e) => {
    if (e.kind === "concern" || e.kind === "signal" || e.kind === "escalation") {
      return false;
    }
    if (e.kind === "alternative") {
      alts += 1;
      return alts <= 3;
    }
    return true;
  });
}
