"use client";

export type Beat = {
  id: string;
  step: string;
  title: string;
  line: string;
};

export const BEATS: Beat[] = [
  {
    id: "intake",
    step: "01",
    title: "A Jira ticket lands",
    line: "Routed to the owning SME — no central orchestrator.",
  },
  {
    id: "investigate",
    step: "02",
    title: "Owner investigates",
    line: "MirrorMaker reads runbooks, forms findings, maps blast radius.",
  },
  {
    id: "discover",
    step: "03",
    title: "Discover who to bring in",
    line: "Consult teams that own the impact. Skip everyone else.",
  },
  {
    id: "deliberate",
    step: "04",
    title: "Peers talk on the mesh",
    line: "Asks and replies fly between specialists until the design holds.",
  },
  {
    id: "decide",
    step: "05",
    title: "Land the 1-pager",
    line: "Decision brief: consulted, skipped, recommendation.",
  },
];

type Props = {
  beat: Beat | null;
};

/** Lower-third caption — demo keeps running underneath. */
export default function BeatCaption({ beat }: Props) {
  if (!beat) return null;
  return (
    <div className="beat-caption" key={beat.id} role="status">
      <div className="beat-caption-inner">
        <span className="beat-step">{beat.step}</span>
        <div className="beat-copy">
          <div className="beat-title">{beat.title}</div>
          <div className="beat-line">{beat.line}</div>
        </div>
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
