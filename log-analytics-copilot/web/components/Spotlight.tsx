"use client";

import { agentColor, agentName, agentShort } from "@/lib/types";

export type SpotlightItem = {
  seq: number;
  to: string;
  consult: boolean;
  reason: string;
};

type Props = {
  from: string;
  items: SpotlightItem[];
  visible: boolean;
};

/** Non-blocking discovery rail — mesh stays visible so hops still read. */
export default function Spotlight({ from, items, visible }: Props) {
  if (!visible || items.length === 0) return null;
  const consults = items.filter((i) => i.consult);
  const skips = items.filter((i) => !i.consult);
  const latest = items[items.length - 1];

  return (
    <div className="spot-rail" role="status">
      <div className="spot-rail-head">
        <span className="spot-rail-dot" />
        <div>
          <div className="spot-rail-kicker">Discovery</div>
          <div className="spot-rail-title">
            {agentName(from)} mapping blast radius
          </div>
        </div>
        {latest ? (
          <div
            className={`spot-latest ${latest.consult ? "consult" : "skip"}`}
            style={{ ["--agent" as string]: agentColor(latest.to) }}
          >
            <span className="badge" style={{ background: agentColor(latest.to) }}>
              {agentShort(latest.to)}
            </span>
            <span>{latest.consult ? "Consult" : "Skip"}</span>
          </div>
        ) : null}
      </div>
      <div className="spot-rail-cols">
        <div className="spot-rail-col">
          <div className="spot-rail-label">Consult · {consults.length}</div>
          <div className="spot-rail-chips">
            {consults.map((i) => (
              <div className="spot-chip consult" key={i.seq} title={i.reason}>
                <span className="badge" style={{ background: agentColor(i.to) }}>
                  {agentShort(i.to)}
                </span>
                <span>{agentName(i.to)}</span>
              </div>
            ))}
            {consults.length === 0 ? <span className="spot-none">—</span> : null}
          </div>
        </div>
        <div className="spot-rail-col">
          <div className="spot-rail-label">Skip · {skips.length}</div>
          <div className="spot-rail-chips">
            {skips.map((i) => (
              <div className="spot-chip skip" key={i.seq} title={i.reason}>
                <span className="badge" style={{ background: agentColor(i.to) }}>
                  {agentShort(i.to)}
                </span>
                <span>{agentName(i.to)}</span>
              </div>
            ))}
            {skips.length === 0 ? <span className="spot-none">—</span> : null}
          </div>
        </div>
      </div>
    </div>
  );
}
