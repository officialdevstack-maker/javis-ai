import { useEffect, useState } from "react";
import {
  getAuditLog,
  getAutonomyStatus,
  updateKillSwitch,
} from "./api";
import type { AuditEntry, AutonomyStatus, Scope } from "./api";

interface SafetyProps {
  onBack: () => void;
}

export default function Safety({ onBack }: SafetyProps) {
  const [status, setStatus] = useState<AutonomyStatus | null>(null);
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [scope, setScope] = useState<Scope | "">("");
  const [toolName, setToolName] = useState("");
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([
      getAutonomyStatus(),
      getAuditLog(scope || undefined, toolName || undefined),
    ])
      .then(([autonomy, audit]) => {
        if (!active) return;
        setStatus(autonomy);
        setEntries(audit);
        setError(null);
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "Could not load safety settings.");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [scope, toolName]);

  async function toggleKillSwitch(): Promise<void> {
    if (!status || updating) return;
    setUpdating(true);
    setError(null);
    try {
      setStatus(await updateKillSwitch(!status.kill_switch_enabled));
    } catch (updateError: unknown) {
      setError(updateError instanceof Error ? updateError.message : "Could not update the kill switch.");
    } finally {
      setUpdating(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Javis home">
          <span className="brand-mark" aria-hidden="true">J</span>
          <span>javis<span className="brand-ai">.ai</span></span>
        </a>
        <div className="header-actions">
          <span className="local-indicator"><span /> Local and private</span>
          <button className="new-chat-button" onClick={onBack}>Back to chat</button>
        </div>
      </header>

      <section className="safety-page">
        <div className="sources-heading">
          <p className="eyebrow">PERMISSIONS & HISTORY</p>
          <h1>Safety and audit</h1>
          <p>Review tool permissions and activity. Tool arguments and results are stored locally in the audit log.</p>
        </div>

        <section className="safety-card" aria-labelledby="kill-switch-heading">
          <div>
            <h2 id="kill-switch-heading">Autonomy kill switch</h2>
            <p>
              {status?.kill_switch_enabled
                ? "Enabled — tier 2 and tier 3 tools are blocked."
                : "Disabled — tier 2 and tier 3 tools still require approval before actions."}
            </p>
          </div>
          <button
            className={status?.kill_switch_enabled ? "kill-switch-button active" : "kill-switch-button"}
            onClick={() => void toggleKillSwitch()}
            disabled={!status || updating}
          >
            {updating ? "Updating…" : status?.kill_switch_enabled ? "Disable switch" : "Enable switch"}
          </button>
        </section>

        <section className="safety-card connector-status" aria-labelledby="connectors-heading">
          <div>
            <h2 id="connectors-heading">Connectors</h2>
            <p>No external connectors are configured. New connectors should keep credentials in an OS keyring or encrypted store.</p>
          </div>
          <span className="connector-badge">Local only</span>
        </section>

        <section className="safety-section" aria-labelledby="tools-heading">
          <h2 id="tools-heading">Tool permissions</h2>
          <div className="tool-policy-list">
            {status?.tools.map((tool) => (
              <article className="tool-policy" key={tool.name}>
                <strong>{tool.name}</strong>
                <span>Tier {tool.tier}</span>
                <span>{tool.is_read_only ? "Read-only" : "Action"}</span>
                <span>{tool.requires_confirmation ? "Approval required" : "No approval required"}</span>
              </article>
            ))}
          </div>
        </section>

        <section className="safety-section" aria-labelledby="audit-heading">
          <div className="audit-heading">
            <h2 id="audit-heading">Tool call audit log</h2>
            <div className="audit-filters">
              <label>
                <span>Scope</span>
                <select value={scope} onChange={(event) => setScope(event.target.value as Scope | "")}>
                  <option value="">All scopes</option>
                  <option value="private">Private</option>
                  <option value="public">Public</option>
                  <option value="business">Business</option>
                </select>
              </label>
              <label>
                <span>Tool</span>
                <select value={toolName} onChange={(event) => setToolName(event.target.value)}>
                  <option value="">All tools</option>
                  {status?.tools.map((tool) => <option key={tool.name} value={tool.name}>{tool.name}</option>)}
                </select>
              </label>
            </div>
          </div>
          {loading ? (
            <p className="audit-empty">Loading audit history…</p>
          ) : entries.length === 0 ? (
            <p className="audit-empty">No tool calls match these filters.</p>
          ) : (
            <div className="audit-list">
              {entries.map((entry) => (
                <article className="audit-entry" key={entry.id}>
                  <div className="audit-entry-heading">
                    <strong>{entry.tool_name}</strong>
                    <span className={`audit-status ${entry.status}`}>{entry.status}</span>
                    <span>{entry.scope} · tier {entry.tier}</span>
                    <time dateTime={entry.created_at}>{new Date(entry.created_at).toLocaleString()}</time>
                  </div>
                  <p className="audit-approval">
                    Approval: {entry.approval_by ?? "not required / not granted"}
                  </p>
                  <details>
                    <summary>Arguments and result</summary>
                    <pre>{JSON.stringify(entry.arguments, null, 2)}</pre>
                    <pre>{entry.result ?? "Tool execution is still in progress."}</pre>
                  </details>
                </article>
              ))}
            </div>
          )}
        </section>
        {error && <p className="error-message" role="alert">{error}</p>}
      </section>
    </main>
  );
}
