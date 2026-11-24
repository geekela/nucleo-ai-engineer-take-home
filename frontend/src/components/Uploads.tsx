import type { RunView } from "../api";
import { clockTime, isInProgress, statusLine } from "../format";
import StatusIcon from "./Icon";

interface Props {
  runs: RunView[];
  selected: string | null;
  now: number; // seconds, for countdowns and elapsed times
  notices: string[]; // files that were never sent, and why
  onSelect: (runId: string) => void;
  onCancel: (runId: string) => void;
  onDismiss: (index: number) => void;
  onClearFailed: () => void;
}

const selectable = (r: RunView) => r.status === "done" && !r.warning && !r.cleared;

function RunBody({ run, now }: { run: RunView; now: number }) {
  const dice = run.dice;
  return (
    <>
      <span className="run-line">
        <StatusIcon status={run.status} />
        <span className="run-source">{run.source}</span>
        <span className="run-time">{clockTime(run.created_at)}</span>
      </span>
      <span className={`run-status ${run.status}`}>{statusLine(run, now)}</span>
      {run.status === "uploading" && run.upload_fraction != null && (
        <span
          className="progress"
          role="progressbar"
          aria-label={`Sending ${run.source}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(run.upload_fraction * 100)}
        >
          <span style={{ width: `${run.upload_fraction * 100}%` }} />
        </span>
      )}
      {dice && dice.value != null && <span className="run-dsc">DSC {dice.value.toFixed(3)}</span>}
    </>
  );
}

export default function Uploads({ runs, selected, now, notices, onSelect, onCancel, onDismiss, onClearFailed }: Props) {
  const anyFailed = runs.some((r) => r.status === "failed" || r.status === "cancelled");
  return (
    <section className="uploads" aria-labelledby="uploads-title">
      <div className="section-head">
        <h2 id="uploads-title">
          Uploads {runs.length > 0 && <span className="count">{runs.length}</span>}
        </h2>
        {anyFailed && (
          <button type="button" className="text" onClick={onClearFailed}>
            Clear failed
          </button>
        )}
      </div>
      {notices.map((n, i) => (
        <div key={`${i}-${n}`} className="notice" role="alert">
          <span>{n}</span>
          <button type="button" className="text" onClick={() => onDismiss(i)}>
            Dismiss
          </button>
        </div>
      ))}
      {runs.length === 0 ? (
        <p className="empty">No uploads yet. Upload case-001, or drop zip files anywhere on this page to try other scans.</p>
      ) : (
        <ul className="run-list">
          {runs.map((run) => {
            const isSelected = run.run_id === selected;
            return (
              <li key={run.run_id} className={`run ${run.status}${isSelected ? " selected" : ""}`}>
                {selectable(run) ? (
                  <button type="button" className="run-main" aria-pressed={isSelected} onClick={() => onSelect(run.run_id)}>
                    <span className="sr-only">Show </span>
                    <RunBody run={run} now={now} />
                  </button>
                ) : (
                  <div className="run-main">
                    <RunBody run={run} now={now} />
                  </div>
                )}
                {isInProgress(run) && (
                  <button type="button" className="text cancel" aria-label={`Cancel upload of ${run.source}`} onClick={() => onCancel(run.run_id)}>
                    Cancel
                  </button>
                )}
                {run.message && <p className={run.status === "failed" ? "run-message error" : "run-message"}>{run.message}</p>}
                {run.warning && <p className="run-message warning">{run.warning}</p>}
                {run.detail && (
                  <details className="run-detail">
                    <summary>Technical details</summary>
                    <code>
                      {run.detail} · upload {run.run_id.slice(0, 8)}
                    </code>
                  </details>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
