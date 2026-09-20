import {
  LIVE_AGENTS,
  agentColor,
  agentName,
  agentShort,
  type TraceEvent,
} from "./types";

export type LaneMood =
  | "idle"
  | "thinking"
  | "asking"
  | "replying"
  | "reading"
  | "done";

export type LaneState = {
  id: string;
  mood: LaneMood;
  status: string;
  processing: string;
  thought: string;
  consulted: boolean;
};

export type Packet = {
  id: string;
  from: string;
  to: string;
  kind: "request" | "response" | "consult" | "skip";
  color: string;
  seq: number;
  kicker: string;
  blurb: string;
};

const STOP = new Set([
  "i", "a", "an", "the", "and", "or", "to", "of", "in", "on", "for", "that",
  "this", "is", "are", "we", "it", "as", "at", "be", "by", "with", "from",
  "have", "has", "my", "our", "your", "me", "was", "were",
]);

/** Cut on a word boundary. Never append ellipsis or trailing dashes. */
export function completePhrase(text: string, max = 88): string {
  const clean = String(text || "")
    .replace(/[()[\]"`_]/g, " ")
    .replace(/\s+/g, " ")
    .replace(/\.{2,}/g, " ")
    .trim();
  if (!clean) return "";
  const sentence = clean.split(/(?<=[.!?])\s+/)[0] || clean;
  if (sentence.length <= max) {
    return sentence.replace(/[,:;.—-]+$/, "").replace(/[.]$/, "");
  }
  const clause = sentence.split(/[,;—–] /)[0] || sentence;
  const clipped = clause.length <= max ? clause : takeWords(clause, max);
  return clipped.replace(/[,:;.—-]+$/, "").replace(/[.]$/, "");
}

function takeWords(text: string, max: number): string {
  const words = text.split(" ").filter(Boolean);
  let out = "";
  for (const w of words) {
    const next = out ? `${out} ${w}` : w;
    if (next.length > max) break;
    out = next;
  }
  return out || words[0] || "";
}

/** Compact signal text: keep original word order, drop filler, finish the phrase. */
export function shortSignal(text: string, maxWords = 6): string {
  const clean = String(text || "")
    .replace(/[()[\]"`_]/g, " ")
    .replace(/\s+/g, " ")
    .replace(/\.{2,}/g, " ")
    .trim();
  if (!clean) return "";
  const words = clean
    .split(" ")
    .map((w) => w.replace(/[,:;.—]+$/g, ""))
    .filter(Boolean);
  const kept: string[] = [];
  for (const w of words) {
    if (STOP.has(w.toLowerCase()) && kept.length > 0) continue;
    kept.push(w);
    if (kept.length >= maxWords) break;
  }
  return (kept.length ? kept : words.slice(0, maxWords)).join(" ");
}

export function packetBlurb(e: TraceEvent): string {
  if (e.kind === "response") {
    const verdict = e.data.verdict;
    if (typeof verdict === "string" && verdict.trim()) {
      return verdict.replace(/_/g, " ");
    }
    const summary = e.data.summary;
    if (typeof summary === "string" && summary.trim()) {
      return shortSignal(summary, 5);
    }
  }
  if (e.kind === "request") {
    const concern = e.data.concern;
    if (typeof concern === "string" && concern.trim()) {
      return shortSignal(concern, 5);
    }
    const title = (e.title || e.detail || "")
      .replace(/^ask(ing)?\s+/i, "")
      .replace(/\s+about\s+/i, " ");
    return shortSignal(title, 5) || `Query ${agentShort(e.to_agent)}`;
  }
  if (e.kind === "peer_decision") {
    if (e.data.consult) {
      return `Bring in ${agentShort(e.to_agent)}`;
    }
    return `Skip ${agentShort(e.to_agent)}`;
  }
  return shortSignal(e.detail || e.title, 5);
}

export function thoughtFrom(e: TraceEvent): string {
  const summary = e.data.summary;
  if (typeof summary === "string" && summary.trim()) {
    return completePhrase(summary, 140);
  }
  const recommendation = e.data.recommendation;
  if (typeof recommendation === "string" && recommendation.trim()) {
    return completePhrase(recommendation, 140);
  }
  return completePhrase(e.detail || e.title, 140);
}

export function processingFrom(e: TraceEvent): string {
  switch (e.kind) {
    case "ticket":
      return "Picking up the ticket";
    case "tool_call":
      return `Running ${e.title}`;
    case "finding":
      return "Forming a finding";
    case "alternative":
      return "Drafting an alternative";
    case "signal":
      return "Weighing who is affected";
    case "peer_decision":
      return e.data.consult
        ? `Wiring in ${agentName(e.to_agent)}`
        : `Skipping ${agentName(e.to_agent)}`;
    case "request":
      return `Asking ${agentName(e.to_agent)}`;
    case "response": {
      const verdict = e.data.verdict;
      return typeof verdict === "string"
        ? `Answering with ${verdict.replace(/_/g, " ")}`
        : `Answering ${agentName(e.to_agent)}`;
    }
    case "concern":
      return "Raising a concern";
    default:
      return e.title;
  }
}

export function nowCopy(events: TraceEvent[]): string {
  const e = events.at(-1);
  if (!e) return "Mesh idle";
  switch (e.kind) {
    case "ticket":
      return `${agentName(e.agent)} picked up the ticket`;
    case "tool_call":
      return `${agentName(e.agent)} is running ${e.title}`;
    case "finding":
      return `${agentName(e.agent)} is writing a finding`;
    case "alternative":
      return `${agentName(e.agent)} is drafting an alternative`;
    case "signal":
      return `${agentName(e.agent)} is mapping who is affected`;
    case "peer_decision":
      return e.data.consult
        ? `${agentName(e.agent)} is wiring in ${agentName(e.to_agent)}`
        : `${agentName(e.agent)} is skipping ${agentName(e.to_agent)}`;
    case "request":
      return `${agentName(e.agent)} is asking ${agentName(e.to_agent)}`;
    case "response":
      return `${agentName(e.agent)} is answering ${agentName(e.to_agent)}`;
    case "concern":
      return `${agentName(e.agent)} raised a concern`;
    default:
      return completePhrase(e.title, 72);
  }
}

function statusFor(mood: LaneMood): string {
  switch (mood) {
    case "thinking":
      return "Thinking";
    case "asking":
      return "Asking";
    case "replying":
      return "Replying";
    case "reading":
      return "Reading";
    case "done":
      return "Signed off";
    default:
      return "Idle";
  }
}

export function deriveLanes(events: TraceEvent[], done: boolean): LaneState[] {
  const lastByAgent = new Map<string, TraceEvent>();
  const thoughtByAgent = new Map<string, string>();
  const consulted = new Set<string>(["mirrormaker"]);

  for (const e of events) {
    lastByAgent.set(e.agent, e);
    if (e.kind === "peer_decision" && e.data.consult) consulted.add(e.to_agent);
    if (e.kind === "request") consulted.add(e.to_agent);
    if (e.kind === "response") consulted.add(e.agent);
    if (
      e.kind === "finding" ||
      e.kind === "concern" ||
      e.kind === "alternative" ||
      e.kind === "tool_call" ||
      e.kind === "ticket"
    ) {
      thoughtByAgent.set(e.agent, thoughtFrom(e));
    }
  }

  const latest = events.at(-1);
  const actor = latest?.agent || "mirrormaker";

  return LIVE_AGENTS.map((id) => {
    const last = lastByAgent.get(id);
    let mood: LaneMood = "idle";
    let processing = consulted.has(id) ? "On the mesh" : "Standing by";
    let thought = thoughtByAgent.get(id) ?? "";

    if (!consulted.has(id) && id !== "mirrormaker") {
      processing = events.length ? "Not on this ticket" : "Standing by";
    }

    if (latest && id === actor) {
      if (latest.kind === "request") mood = "asking";
      else if (latest.kind === "response") mood = "replying";
      else mood = "thinking";
      processing = processingFrom(latest);
      thought = thoughtFrom(latest);
    } else if (latest?.kind === "request" && latest.to_agent === id) {
      mood = "reading";
      processing = `Incoming from ${agentShort(latest.agent)}`;
      thought = thoughtFrom(latest);
    } else if (latest?.kind === "response" && latest.to_agent === id) {
      mood = "reading";
      processing = `Reply from ${agentShort(latest.agent)}`;
      thought = thoughtFrom(latest);
    } else if (latest?.kind === "peer_decision" && latest.to_agent === id) {
      mood = latest.data.consult ? "reading" : "idle";
      processing = latest.data.consult ? "Being wired in" : "Skipped";
      thought = thoughtFrom(latest);
    } else if (last && consulted.has(id)) {
      processing = "On call";
    }

    if (done && consulted.has(id)) {
      mood = "done";
      if (id === actor) processing = "Signed off";
    }

    return {
      id,
      mood,
      status: statusFor(mood),
      processing,
      thought:
        thought ||
        (consulted.has(id)
          ? "Waiting for the next step on this ticket"
          : "No work on this ticket"),
      consulted: consulted.has(id),
    };
  });
}

function hopKicker(e: TraceEvent, kind: Packet["kind"]): string {
  const arrow = `${agentShort(e.agent)} → ${agentShort(e.to_agent)}`;
  if (kind === "response") {
    const verdict =
      typeof e.data.verdict === "string" ? e.data.verdict.replace(/_/g, " ") : "reply";
    return `${arrow}  ${verdict}`;
  }
  if (kind === "consult") return `${arrow}  consult`;
  if (kind === "skip") return `${arrow}  skip`;
  return `${arrow}  ask`;
}

export function packetFromEvent(e: TraceEvent): Packet | null {
  const live = (id: string | undefined) =>
    !!id && LIVE_AGENTS.includes(id as (typeof LIVE_AGENTS)[number]);

  if (e.kind === "request" && live(e.agent) && live(e.to_agent)) {
    return {
      id: `p-${e.seq}-req`,
      from: e.agent,
      to: e.to_agent,
      kind: "request",
      color: agentColor(e.agent),
      seq: e.seq,
      kicker: hopKicker(e, "request"),
      blurb: packetBlurb(e),
    };
  }
  if (e.kind === "response" && live(e.agent) && live(e.to_agent)) {
    return {
      id: `p-${e.seq}-res`,
      from: e.agent,
      to: e.to_agent,
      kind: "response",
      color: agentColor(e.agent),
      seq: e.seq,
      kicker: hopKicker(e, "response"),
      blurb: packetBlurb(e),
    };
  }
  if (e.kind === "peer_decision" && live(e.agent) && live(e.to_agent)) {
    const kind = e.data.consult ? "consult" : "skip";
    return {
      id: `p-${e.seq}-dec`,
      from: e.agent,
      to: e.to_agent,
      kind,
      color: agentColor(e.agent),
      seq: e.seq,
      kicker: hopKicker(e, kind),
      blurb: packetBlurb(e),
    };
  }
  return null;
}

export function skippedFrom(events: TraceEvent[]): TraceEvent[] {
  return events.filter(
    (e) => e.kind === "peer_decision" && e.data.consult === false,
  );
}

export function consultedFrom(events: TraceEvent[]): TraceEvent[] {
  return events.filter(
    (e) => e.kind === "peer_decision" && e.data.consult === true,
  );
}

export function currentHop(events: TraceEvent[]): Packet | null {
  const e = events.at(-1);
  return e ? packetFromEvent(e) : null;
}

export function actingAgent(events: TraceEvent[]): string {
  const e = events.at(-1);
  if (e?.agent && LIVE_AGENTS.includes(e.agent as (typeof LIVE_AGENTS)[number])) {
    return e.agent;
  }
  const ticket = events.find((x) => x.kind === "ticket");
  return ticket?.agent || "mirrormaker";
}

export function interactionLog(events: TraceEvent[]): TraceEvent[] {
  return events.filter(
    (e) =>
      e.kind === "request" ||
      e.kind === "response" ||
      e.kind === "peer_decision",
  );
}

export function meshLinks(events: TraceEvent[]): Array<[string, string]> {
  const set = new Set<string>();
  const add = (a: string, b: string) => {
    if (!a || !b || a === b) return;
    set.add([a, b].sort().join("|"));
  };
  for (const e of events) {
    if (e.kind === "request" || e.kind === "response") add(e.agent, e.to_agent);
    if (e.kind === "peer_decision" && e.data.consult) add(e.agent, e.to_agent);
  }
  return [...set].map((k) => k.split("|") as [string, string]);
}

