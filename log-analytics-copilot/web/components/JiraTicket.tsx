"use client";

type Props = {
  keyId: string;
  title: string;
  description: string;
  priority?: string;
  team?: string;
  sourceUrl?: string;
  visible: boolean;
};

export default function JiraTicket({
  keyId,
  title,
  description,
  priority = "High",
  team = "mirrormaker",
  sourceUrl,
  visible,
}: Props) {
  if (!visible) return null;
  return (
    <div className="jira-stage" role="dialog" aria-label="Jira ticket intake">
      <div className="jira-card">
        <header className="jira-head">
          <div className="jira-brand">
            <span className="jira-logo" aria-hidden>
              J
            </span>
            <span>Jira · issue intake</span>
          </div>
          <a
            className="jira-key"
            href={sourceUrl || "#"}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => {
              if (!sourceUrl) e.preventDefault();
            }}
          >
            {keyId}
          </a>
        </header>
        <h2 className="jira-title">{title}</h2>
        <div className="jira-meta">
          <span className="jira-pill priority">{priority}</span>
          <span className="jira-pill">Owner · {team}</span>
          <span className="jira-pill">Type · Improvement</span>
        </div>
        <p className="jira-desc">{description}</p>
        <footer className="jira-foot">
          <span className="jira-pulse" />
          Routing into the SME mesh
        </footer>
      </div>
    </div>
  );
}
