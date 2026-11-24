import type { RunStatus, RunView } from "./api";

const LABEL: Record<RunStatus, string> = {
  queued: "Waiting for a free upload slot",
  uploading: "Uploading",
  segmenting: "Segmenting",
  waiting_to_retry: "Service busy. Retrying soon",
  downloading_masks: "Downloading masks and computing Dice",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

/** One line saying what an upload is doing right now. `now` is in seconds, like the server's timestamps. */
export function statusLine(run: RunView, now: number): string {
  if (run.status === "waiting_to_retry" && run.retry_at != null) {
    const left = Math.ceil(run.retry_at - now);
    return left > 0 ? `Service busy. Retrying in ${left} s` : "Service busy. Retrying now";
  }
  if (run.status === "uploading" && run.upload_fraction != null) {
    return `${LABEL.uploading}, ${Math.round(run.upload_fraction * 100)}%`;
  }
  if (run.status === "segmenting") {
    const elapsed = Math.floor(now - run.status_since);
    return elapsed >= 1 ? `${LABEL.segmenting}, ${elapsed} s` : LABEL.segmenting;
  }
  return LABEL[run.status] ?? run.status;
}

export const isInProgress = (r: RunView) => r.status !== "done" && r.status !== "failed" && r.status !== "cancelled";

export const formatMl = (ml: number) => `${ml < 10 ? ml.toFixed(2) : ml.toFixed(1)} mL`;

export const clockTime = (seconds: number) =>
  new Date(seconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
