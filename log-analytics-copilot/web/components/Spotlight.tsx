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

export default function Spotlight({ from, items, visible }: Props) {
  if (!visible || items.length === 0) return null;
  const consults = items.filter((i) => i.consult);
  const skips = items.filter((i) => !i.consult);

  return (
    <div className="spotlight" role="status">
      <div className="spotlight-card">
        <div className="spotlight-kicker">
          <span className="spotlight-dot" />
          Discovery · blast radius
        </div>
        <h2 className="spotlight-title">
          {agentName(from)} is choosing who enters the room
        </h2>
        <p className="spotlight-sub">
          Consult the owners of the blast radius. Skip everyone else.
        </p>
        <div className="spotlight-cols">
          <section>
            <h3>Consult · {consults.length}</h3>
            {consults.length === 0 ? (
              <p className="spotlight-empty">None yet</p>
            ) : (
              consults.map((i) => (
                <div className="spotlight-row consult" key={i.seq}>
                  <span
                    className="badge"
                    style={{ background: agentColor(i.to) }}
                  >
                    {agentShort(i.to)}
                  </span>
                  <div>
                    <div className="name">{agentName(i.to)}</div>
                    <div className="why">{i.reason}</div>
                  </div>
                </div>
              ))
            )}
          </section>
          <section>
            <h3>Skip · {skips.length}</h3>
            {skips.length === 0 ? (
              <p className="spotlight-empty">None yet</p>
            ) : (
              skips.map((i) => (
                <div className="spotlight-row skip" key={i.seq}>
                  <span
                    className="badge"
                    style={{ background: agentColor(i.to) }}
                  >
                    {agentShort(i.to)}
                  </span>
                  <div>
                    <div className="name">{agentName(i.to)}</div>
                    <div className="why">{i.reason}</div>
                  </div>
                </div>
              ))
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
