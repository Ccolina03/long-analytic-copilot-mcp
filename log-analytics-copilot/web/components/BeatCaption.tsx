"use client";

export type Beat = {
  id: string;
  step: string;
  label: string;
  title: string;
  line: string;
};

/** Spoken like an SME on the mesh — first person, from inside the work. */
export const BEATS: Beat[] = [
  {
    id: "intake",
    step: "1",
    label: "Intake",
    title: "This one's mine",
    line: "Jira just routed KAFKA-18231 to me — MirrorMaker. I own the checkpoint path, so I pick it up.",
  },
  {
    id: "investigate",
    step: "2",
    label: "Investigate",
    title: "I'm digging the failure mode",
    line: "I'm reading my runbooks and CODEOWNERS. Group discovery is O(n) over every consumer group — that's the p99 burn.",
  },
  {
    id: "discover",
    step: "3",
    label: "Discover",
    title: "I decide who enters the room",
    line: "I wire in Coordinator, Broker, Clients, Security — they own the blast radius. Storage, Streams, Connect, Tools: I skip them.",
  },
  {
    id: "deliberate",
    step: "4",
    label: "Deliberate",
    title: "We pressure-test the design",
    line: "I ask each peer for impact. They answer across the mesh — needs_changes, concerns, ownership — until we converge.",
  },
  {
    id: "decide",
    step: "5",
    label: "Decide",
    title: "Here's the brief I'd hand a human",
    line: "One page: who I consulted, who I skipped, and the recommendation. You still decide — I just did the routing.",
  },
];

/** How long each step banner stays on screen (in + hold + out). */
export const BEAT_MS = 3000;

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
          <span className="beat-banner-voice">SME · MirrorMaker</span>
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
