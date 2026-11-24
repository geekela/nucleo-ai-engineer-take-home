import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  cancelRun,
  clearFailedRuns,
  getCtMeta,
  getCtVolume,
  getReferenceMask,
  getStatus,
  getStructureMask,
  listRuns,
  startBundledRun,
  startFileRun,
  type CtMeta,
  type Result,
  type RunView,
  type Status,
} from "./api";
import ComparePanel from "./components/ComparePanel";
import Header from "./components/Header";
import Layers, { type Layer } from "./components/Layers";
import StatusBar from "./components/StatusBar";
import type { Lane } from "./components/SliceProfile";
import Uploads from "./components/Uploads";
import Viewer from "./components/Viewer";
import { isInProgress } from "./format";
import { MaskCache } from "./maskCache";
import { buildOverlays, type ViewMode } from "./overlays";
import { modelColour, REFERENCE_RGB } from "./palette";
import { sliceCounts } from "./profile";
import type { Dims } from "./render";

const REFERENCE_KEY = "reference";
const ACTIVE_POLL_MS = 1000;
const IDLE_POLL_MS = 5000;

const selectable = (r: RunView) => r.status === "done" && !r.warning && !r.cleared;
// Toggles follow a structure across uploads by position and name, so hiding "lesions" stays hidden.
const modelKey = (index: number, name: string) => `model:${index}:${name}`;
const nowSeconds = () => Date.now() / 1000;

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [offline, setOffline] = useState<string | null>(null); // set while the local server is unreachable
  const [meta, setMeta] = useState<CtMeta | null>(null);
  const [volume, setVolume] = useState<Int16Array | null>(null);
  const [ctError, setCtError] = useState<string | null>(null);
  const [reference, setReference] = useState<Uint8Array | null>(null);
  const [slice, setSlice] = useState(0);

  const [runs, setRuns] = useState<RunView[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [notices, setNotices] = useState<string[]>([]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<ViewMode>("overlay");
  const [opacity, setOpacity] = useState(0.45);
  const [now, setNow] = useState(nowSeconds);
  const [dragging, setDragging] = useState(false);
  const [pollNudge, setPollNudge] = useState(0);
  const [, setMaskVersion] = useState(0);
  const cache = useMemo(() => new MaskCache(() => setMaskVersion((v) => v + 1)), []);
  const baseLoading = useRef(false);
  const knownDone = useRef(new Set<string>());

  const loadBase = useCallback(async () => {
    if (baseLoading.current) return;
    baseLoading.current = true;
    try {
      const s = await getStatus();
      if (!s.ok) {
        setOffline(s.offline ? s.message : null);
        if (!s.offline) setCtError(s.message);
        return;
      }
      setStatus(s.value);
      if (!s.value.ct.ok) {
        setCtError(s.value.ct.message);
        return;
      }
      setCtError(null);
      const [m, v] = await Promise.all([getCtMeta(), getCtVolume()]);
      if (!m.ok || !v.ok) {
        setCtError((!m.ok ? m.message : !v.ok ? v.message : null) ?? "Couldn't load the CT.");
        return;
      }
      setMeta(m.value);
      setVolume(v.value);
      setSlice(Math.floor(m.value.shape[0] / 2));
      if (s.value.reference.ok) {
        const r = await getReferenceMask();
        if (r.ok) setReference(r.value);
      }
    } finally {
      baseLoading.current = false;
    }
  }, []);

  useEffect(() => {
    loadBase();
  }, [loadBase]);

  // Retry the first load once the local server is reachable again.
  useEffect(() => {
    if (!offline && !status) loadBase();
  }, [offline, status, loadBase]);

  // Poll uploads: every second while any is in progress, every 5 s otherwise. Never stops on errors.
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      const r = await listRuns();
      if (stopped) return;
      if (r.ok) {
        setRuns(r.value);
        setOffline(null);
      } else if (r.offline) {
        setOffline(r.message);
      }
      const fast = !r.ok || r.value.some(isInProgress);
      timer = setTimeout(tick, fast ? ACTIVE_POLL_MS : IDLE_POLL_MS);
    };
    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [pollNudge]);

  // A clock for countdowns and elapsed times, running only while something is in progress.
  const anyInProgress = runs.some(isInProgress);
  useEffect(() => {
    if (!anyInProgress) return;
    setNow(nowSeconds());
    const t = setInterval(() => setNow(nowSeconds()), 250);
    return () => clearInterval(t);
  }, [anyInProgress]);

  // Show an upload's result as soon as it finishes; otherwise keep the user's choice while it lasts.
  useEffect(() => {
    const done = runs.filter(selectable);
    const fresh = done.find((r) => !knownDone.current.has(r.run_id));
    for (const r of done) knownDone.current.add(r.run_id);
    if (fresh) setSelected(fresh.run_id);
    else if (!selected || !done.some((r) => r.run_id === selected)) setSelected(done[0]?.run_id ?? null);
  }, [runs, selected]);

  // Forget masks of uploads the server has cleared.
  useEffect(() => {
    cache.retain(new Set(runs.filter((r) => !r.cleared).map((r) => r.run_id)));
  }, [runs, cache]);

  const selectedRun = runs.find((r) => r.run_id === selected) ?? null;

  // Download the selected upload's visible masks, once each.
  useEffect(() => {
    if (!selectedRun || !volume) return;
    const id = selectedRun.run_id;
    selectedRun.structures.forEach((s, i) => {
      if (s.overlay_ok && !hidden.has(modelKey(i, s.name))) cache.request(id, i, () => getStructureMask(id, i));
    });
  }, [selectedRun, volume, hidden, cache]);

  const dims: Dims | null = useMemo(() => (meta ? { slices: meta.shape[0], rows: meta.shape[1], cols: meta.shape[2] } : null), [meta]);

  const models = (selectedRun?.structures ?? []).map((s, i) => {
    const entry = selectedRun ? cache.get(selectedRun.run_id, i) : undefined;
    return { s, i, key: modelKey(i, s.name), entry, mask: entry?.state === "ok" ? entry.mask : null };
  });
  const comparedIndex = models.findIndex((m) => m.s.compared && m.s.overlay_ok);
  const compared = comparedIndex >= 0 && models[comparedIndex].mask !== null ? models[comparedIndex] : null;
  const canCompare = compared !== null && reference !== null;
  const viewMode: ViewMode = canCompare ? mode : "overlay";

  const layers: Layer[] = [
    ...models.map(({ s, i, key, entry }) => ({
      key,
      label: `Model: ${s.name}`,
      rgb: modelColour(i),
      style: "fill" as const,
      available: s.overlay_ok && entry?.state !== "error",
      checked: !hidden.has(key),
      note: !s.overlay_ok ? s.message : entry?.state === "error" ? entry.message : entry?.state === "loading" ? "Downloading…" : s.message,
      onRetry:
        selectedRun && entry?.state === "error"
          ? () => {
              const id = selectedRun.run_id;
              cache.forget(id, i);
              cache.request(id, i, () => getStructureMask(id, i));
            }
          : undefined,
    })),
    {
      key: REFERENCE_KEY,
      label: "Reference (ground truth)",
      rgb: REFERENCE_RGB,
      style: "outline",
      available: reference !== null,
      checked: !hidden.has(REFERENCE_KEY),
      note: reference ? null : (status?.reference.message ?? "Reference mask not loaded."),
    },
  ];

  // Everything the overlays depend on, as one value. The selected upload is part of it, so switching
  // uploads always redraws, even when the new one's masks are already cached.
  const overlaySignature = JSON.stringify([selected, models.map((m) => [m.key, m.s.overlay_ok, !!m.s.compared, m.mask !== null])]);
  const overlays = useMemo(
    () =>
      buildOverlays({
        mode: viewMode,
        opacity,
        models: models.map(({ s, i, key, mask }) => ({ mask, rgb: modelColour(i), visible: s.overlay_ok && !hidden.has(key), compared: !!s.compared })),
        reference: { mask: reference, visible: !hidden.has(REFERENCE_KEY) },
      }),
    // `models` is rebuilt every render; overlaySignature captures every part of it that matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [viewMode, opacity, reference, hidden, overlaySignature],
  );

  const comparedMask = compared?.mask ?? null;
  const modelCounts = useMemo(() => (comparedMask && dims ? sliceCounts(comparedMask, dims) : null), [comparedMask, dims]);
  const referenceCounts = useMemo(() => (reference && dims ? sliceCounts(reference, dims) : null), [reference, dims]);
  const lanes: Lane[] = useMemo(() => {
    const out: Lane[] = [];
    if (modelCounts && comparedIndex >= 0) out.push({ label: "Model", rgb: modelColour(comparedIndex), counts: modelCounts });
    if (referenceCounts) out.push({ label: "Reference", rgb: REFERENCE_RGB, counts: referenceCounts });
    return out;
  }, [modelCounts, referenceCounts, comparedIndex]);

  const toggle = (key: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const afterStart = useCallback((r: Result<RunView>) => {
    if (r.ok) setRuns((prev) => [r.value, ...prev.filter((x) => x.run_id !== r.value.run_id)]);
    else setNotices((prev) => [...prev, r.message]);
    setPollNudge((n) => n + 1);
  }, []);

  const uploadFiles = useCallback((files: File[]) => files.forEach((f) => startFileRun(f).then(afterStart)), [afterStart]);

  const cancel = (runId: string) =>
    cancelRun(runId).then((r) => {
      if (r.ok) setRuns((prev) => prev.map((x) => (x.run_id === runId ? r.value : x)));
      else setNotices((prev) => [...prev, r.message]);
      setPollNudge((n) => n + 1);
    });

  const clearFailed = () =>
    clearFailedRuns().then((r) => {
      if (r.ok) setRuns((prev) => prev.filter((x) => x.status !== "failed" && x.status !== "cancelled"));
      else setNotices((prev) => [...prev, r.message]);
      setPollNudge((n) => n + 1);
    });

  // Drop zip files anywhere on the page to upload them.
  const uploadRef = useRef(uploadFiles);
  uploadRef.current = uploadFiles;
  useEffect(() => {
    let depth = 0;
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const enter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth += 1;
      setDragging(true);
    };
    const over = (e: DragEvent) => {
      if (hasFiles(e)) e.preventDefault();
    };
    const leave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) setDragging(false);
    };
    const drop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      setDragging(false);
      const dt = e.dataTransfer;
      if (!dt) return;
      const files: File[] = [];
      const folders: string[] = [];
      Array.from(dt.files).forEach((file, i) => {
        const entry = dt.items?.[i]?.webkitGetAsEntry?.();
        if (entry?.isDirectory) folders.push(entry.name);
        else files.push(file);
      });
      if (folders.length) setNotices((prev) => [...prev, ...folders.map((f) => `${f} is a folder. Upload the zip file instead.`)]);
      if (files.length) uploadRef.current(files);
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, []);

  return (
    <div className="app">
      <Header
        meta={meta}
        canUploadBundled={status?.ct.ok ?? false}
        onUploadBundled={() => startBundledRun().then(afterStart)}
        onUploadFiles={uploadFiles}
      />
      {offline && (
        <p className="banner error" role="alert">
          {offline}
        </p>
      )}
      {status && !status.token_configured && (
        <p className="banner warning" role="alert">
          No API token is set, so uploads will fail. Add NUCLEO_API_TOKEN to .env and restart the app.
        </p>
      )}
      <main className="workspace">
        <section className="viewer-column" aria-label="CT viewer">
          {volume && dims && meta ? (
            <Viewer volume={volume} dims={dims} meta={meta} slice={slice} onSlice={setSlice} overlays={overlays} lanes={lanes} />
          ) : (
            <div className="placeholder">
              <p>{ctError ?? (offline ? "Waiting for the local server…" : "Loading the CT…")}</p>
              {/* Missing data needs a restart, so retrying is offered only when the CT itself is there. */}
              {ctError && (status === null || status.ct.ok) && (
                <button type="button" className="secondary" onClick={loadBase}>
                  Try again
                </button>
              )}
            </div>
          )}
        </section>
        <aside className="sidebar">
          <ComparePanel run={selectedRun} modelRgb={comparedIndex >= 0 ? modelColour(comparedIndex) : undefined} />
          <Layers
            layers={layers}
            onToggle={toggle}
            mode={viewMode}
            onMode={setMode}
            canCompare={canCompare}
            opacity={opacity}
            onOpacity={setOpacity}
          />
          <Uploads
            runs={runs}
            selected={selected}
            now={now}
            notices={notices}
            onSelect={setSelected}
            onCancel={cancel}
            onDismiss={(i) => setNotices((prev) => prev.filter((_, j) => j !== i))}
            onClearFailed={clearFailed}
          />
        </aside>
      </main>
      <StatusBar status={status} />
      {dragging && (
        <div className="drop-overlay" aria-hidden="true">
          <p>Drop zip files to upload them</p>
        </div>
      )}
    </div>
  );
}
