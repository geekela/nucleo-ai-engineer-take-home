/** Typed calls to the local backend. None of these throw: failures come back as { ok: false }. */

export type Result<T> = { ok: true; value: T } | { ok: false; message: string; offline: boolean };

export type RunStatus =
  | "queued"
  | "uploading" // the zip is being sent
  | "segmenting" // all of it is sent; waiting for the service's answer
  | "waiting_to_retry"
  | "downloading_masks"
  | "done"
  | "failed"
  | "cancelled";

export interface StructureView {
  name: string;
  overlay_ok: boolean;
  message: string | null;
  compared?: boolean; // the mask the Dice score compares with the reference
}

export interface LesionCounts {
  reference: number;
  detected: number;
  model: number;
  model_only: number;
}

export interface DiceView {
  value: number | null;
  message: string | null;
  voxels_model?: number;
  voxels_reference?: number;
  voxels_overlap?: number | null;
  ml_model?: number;
  ml_reference?: number;
  shape?: [number, number, number] | null;
  lesions?: LesionCounts | null;
}

export interface RunView {
  run_id: string;
  source: string;
  created_at: number; // seconds since the epoch
  status: RunStatus;
  status_since: number;
  message: string | null;
  detail: string | null;
  retry_in_s: number | null;
  retry_at: number | null;
  upload_fraction?: number | null; // share of the zip sent so far, while uploading
  case_id: string | null;
  warning: string | null;
  cleared: boolean;
  dice: DiceView | null;
  structures: StructureView[];
}

export interface Status {
  ct: { ok: boolean; message: string | null };
  reference: { ok: boolean; message: string | null };
  token_configured: boolean;
  base_url: string;
}

export interface CtMeta {
  shape: [number, number, number]; // (slices, rows, cols)
  spacing_mm: [number, number, number];
  window: { center: number; width: number };
  slice_z_mm?: number[];
  edges?: { left: string; right: string; top: string; bottom: string }; // patient side each image edge faces
}

/** The service refuses uploads over 150 MiB; check before sending so a huge file never leaves the browser. */
export const MAX_UPLOAD_BYTES = 157_286_400;

export const OFFLINE_MESSAGE = "Lost connection to the local server. Is it still running?";
export const UNRESPONSIVE_MESSAGE = "The local server isn't responding. It may be busy or stuck; try restarting it.";
const TIMEOUT_MS = 10_000;
const LONG_TIMEOUT_MS = 60_000; // large local transfers: the CT volume, masks, uploaded files
const GENERIC_MESSAGE = "The local server returned an error.";

async function call<T>(url: string, read: (r: Response) => Promise<T>, init?: RequestInit, timeoutMs = TIMEOUT_MS): Promise<Result<T>> {
  // Every call has a deadline, so a hung local server shows a message instead of leaving the page waiting forever.
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const failed = (): Result<T> => ({ ok: false, offline: true, message: timedOut ? UNRESPONSIVE_MESSAGE : OFFLINE_MESSAGE });
  try {
    let response: Response;
    try {
      response = await fetch(url, { ...init, signal: controller.signal });
    } catch {
      return failed();
    }
    if (!response.ok) {
      let message: string | null = null;
      try {
        const body = await response.json();
        if (body && typeof body.message === "string") message = body.message;
      } catch {
        /* not JSON */
      }
      // Our backend always sends {message}. A 5xx without one comes from the dev proxy: the backend is down or starting.
      if (message === null && response.status >= 500) return { ok: false, offline: true, message: OFFLINE_MESSAGE };
      return { ok: false, offline: false, message: message ?? GENERIC_MESSAGE };
    }
    try {
      return { ok: true, value: await read(response) };
    } catch {
      return timedOut ? failed() : { ok: false, offline: false, message: GENERIC_MESSAGE };
    }
  } finally {
    clearTimeout(timer);
  }
}

const json = <T>(r: Response) => r.json() as Promise<T>;
const bytes = async (r: Response) => new Uint8Array(await r.arrayBuffer());

export const getStatus = () => call<Status>("/api/status", json);
export const getCtMeta = () => call<CtMeta>("/api/ct", json);
export const getCtVolume = () => call("/api/ct/volume", async (r) => new Int16Array(await r.arrayBuffer()), undefined, LONG_TIMEOUT_MS);
export const getReferenceMask = () => call("/api/reference/mask", bytes, undefined, LONG_TIMEOUT_MS);
export const listRuns = () => call<RunView[]>("/api/runs", json);
export const startBundledRun = () => call<RunView>("/api/runs/bundled", json, { method: "POST" });

/** Try to read the first byte. Never throws. */
function canRead(file: File): Promise<boolean> {
  return new Promise((resolve) => {
    try {
      const reader = new FileReader();
      reader.onload = () => resolve(true);
      reader.onerror = () => resolve(false);
      reader.readAsArrayBuffer(file.slice(0, 1));
    } catch {
      resolve(false);
    }
  });
}

export const startFileRun = async (file: File): Promise<Result<RunView>> => {
  if (file.size > MAX_UPLOAD_BYTES) {
    return { ok: false, offline: false, message: `${file.name} is larger than the 150 MB the service accepts, so it wasn't uploaded.` };
  }
  // A folder, or a file moved since it was picked, can't be read; fetch would only say "failed".
  if (!(await canRead(file))) {
    return { ok: false, offline: false, message: `Couldn't read ${file.name}. If it's a folder, choose the zip file inside it.` };
  }
  return call<RunView>("/api/runs", json, {
    method: "POST",
    headers: { "Content-Type": "application/zip", "X-Filename": encodeURIComponent(file.name) },
    body: file,
  }, LONG_TIMEOUT_MS);
};

export const clearFailedRuns = () => call<{ removed: number }>("/api/runs/clear-failed", json, { method: "POST" });

export const cancelRun = (runId: string) => call<RunView>(`/api/runs/${encodeURIComponent(runId)}/cancel`, json, { method: "POST" });

/** Masks are addressed by their position in the upload's structure list, so any name is safe. */
export const getStructureMask = (runId: string, index: number) =>
  call(`/api/runs/${encodeURIComponent(runId)}/structures/${index}`, bytes, undefined, LONG_TIMEOUT_MS); // ~26 MB each
