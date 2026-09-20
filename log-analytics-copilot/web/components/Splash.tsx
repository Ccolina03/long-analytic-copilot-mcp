"use client";

type Props = {
  onStart: () => void;
  onReplay: () => void;
  busy?: boolean;
};

export default function Splash({ onStart, onReplay, busy }: Props) {
  return (
    <div className="splash">
      <div className="splash-glow" aria-hidden />
      <p className="splash-kicker">SME Agent Network</p>
      <h1 className="splash-title">
        Domain experts that discover who owns the blast radius,
        <em> skip everyone else,</em> and converge on a decision.
      </h1>
      <p className="splash-sub">
        A Jira ticket lands. MirrorMaker investigates. Discovery wires in the
        right Kafka teams, skips the rest, deliberates alternatives, and ships
        a one-page design brief — no central orchestrator.
      </p>
      <div className="splash-actions">
        <button className="btn" onClick={onStart} disabled={busy}>
          Run live from ticket
        </button>
        <button className="btn ghost" onClick={onReplay} disabled={busy}>
          Watch recorded demo
        </button>
      </div>
      <ul className="splash-beats">
        <li>
          <span>01</span> Intake from Jira
        </li>
        <li>
          <span>02</span> Discover consults and skips
        </li>
        <li>
          <span>03</span> Land the 1-pager
        </li>
      </ul>
    </div>
  );
}
