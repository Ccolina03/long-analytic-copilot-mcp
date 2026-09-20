export const PHASES = [
  "intake",
  "investigation",
  "discovery",
  "alternatives",
  "deliberation",
  "synthesis",
  "decision",
  "artifact",
] as const;

export type Phase = (typeof PHASES)[number];

export type TraceEvent = {
  seq: number;
  t_ms: number;
  phase: Phase | string;
  agent: string;
  kind: string;
  title: string;
  detail: string;
  round_number: number;
  to_agent: string;
  data: Record<string, unknown>;
};

export type Trace = {
  ticket_id: string;
  title: string;
  phases: string[];
  phases_reached: string[];
  agents: string[];
  event_count: number;
  duration_ms: number;
  events: TraceEvent[];
};

export type TicketSnapshot = {
  id: string;
  status: "running" | "done" | "error";
  ticket: {
    ticket_id: string;
    team: string;
    title: string;
    description: string;
    priority: string;
    source: string;
    source_url: string;
  };
  events: TraceEvent[];
  trace: Trace | null;
  finding: Record<string, unknown> | null;
  doc: string | null;
  error: string | null;
};

export type AgentMeta = {
  short: string;
  name: string;
  color: string;
  live: boolean;
};

export const AGENT_META: Record<string, AgentMeta> = {
  mirrormaker: { short: "MM2", name: "MirrorMaker", color: "#0F7B75", live: true },
  "group-coordinator": { short: "GC", name: "Group Coordinator", color: "#B7791F", live: true },
  "kafka-broker": { short: "BRK", name: "Broker", color: "#2C5282", live: true },
  "kafka-clients": { short: "CLI", name: "Clients", color: "#6B46C1", live: true },
  "kafka-security": { short: "SEC", name: "Security", color: "#C05621", live: true },
  "kafka-storage": { short: "STO", name: "Storage", color: "#8A847C", live: false },
  "kafka-streams": { short: "STR", name: "Streams", color: "#8A847C", live: false },
  "kafka-connect": { short: "CNT", name: "Connect", color: "#8A847C", live: false },
  "kafka-tools": { short: "TL", name: "Tools", color: "#8A847C", live: false },
};

export const LIVE_AGENTS = [
  "mirrormaker",
  "group-coordinator",
  "kafka-broker",
  "kafka-clients",
  "kafka-security",
] as const;

export function agentColor(id: string): string {
  return AGENT_META[id]?.color ?? "#7a8499";
}

export function agentShort(id: string): string {
  return AGENT_META[id]?.short ?? id.slice(0, 3).toUpperCase();
}

export function agentName(id: string): string {
  return AGENT_META[id]?.name ?? id;
}

export const PHASE_COPY: Record<string, { n: string; label: string }> = {
  intake: { n: "01", label: "Intake" },
  investigation: { n: "02", label: "Investigate" },
  discovery: { n: "03", label: "Discover" },
  alternatives: { n: "04", label: "Alternatives" },
  deliberation: { n: "05", label: "Deliberate" },
  synthesis: { n: "06", label: "Synthesize" },
  decision: { n: "07", label: "Decide" },
  artifact: { n: "08", label: "1-pager" },
};
