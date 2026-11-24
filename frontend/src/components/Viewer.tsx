import { useEffect, useRef, useState } from "react";
import type { CtMeta } from "../api";
import { renderSlice, type Dims, type Overlay, type Window } from "../render";
import { wheelStep } from "../wheel";
import SliceProfile, { type Lane } from "./SliceProfile";

interface Props {
  volume: Int16Array;
  dims: Dims;
  meta: CtMeta;
  slice: number;
  onSlice: (s: number) => void;
  overlays: Overlay[];
  lanes: Lane[];
}

interface Preset extends Window {
  key: string;
  label: string;
}

/** Standard CT windows. Soft tissue comes from the backend, the rest are common presets. */
function presets(soft: Window): Preset[] {
  return [
    { key: "soft", label: "Soft tissue", ...soft },
    { key: "liver", label: "Liver", center: 30, width: 150 },
    { key: "bone", label: "Bone", center: 400, width: 1800 },
    { key: "lung", label: "Lung", center: -600, width: 1500 },
  ];
}

const PAGE = 10;
const INTERACTIVE = "input, select, textarea, button, a[href], summary, [contenteditable='true']";

export default function Viewer({ volume, dims, meta, slice, onSlice, overlays, lanes }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const image = useRef<ImageData | null>(null);
  const wheelAcc = useRef(0);
  const sliceRef = useRef(slice);
  sliceRef.current = slice;
  const options = presets(meta.window);
  const [presetKey, setPresetKey] = useState("soft");
  const win = options.find((p) => p.key === presetKey) ?? options[0];
  const clamp = (s: number) => Math.max(0, Math.min(dims.slices - 1, s));

  useEffect(() => {
    const ctx = canvas.current?.getContext("2d");
    if (!ctx) return;
    if (!image.current || image.current.width !== dims.cols || image.current.height !== dims.rows) {
      image.current = ctx.createImageData(dims.cols, dims.rows);
    }
    renderSlice(volume, dims, slice, win, overlays, image.current.data);
    ctx.putImageData(image.current, 0, 0);
  }, [volume, dims, slice, win.center, win.width, overlays]); // eslint-disable-line react-hooks/exhaustive-deps

  // Wheel must be non-passive so the page doesn't scroll while changing slices.
  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const delta = e.deltaMode === 1 ? e.deltaY * 50 : e.deltaY; // lines -> pixels (Firefox)
      const { steps, acc } = wheelStep(wheelAcc.current, delta);
      wheelAcc.current = acc;
      if (steps) onSlice(clamp(sliceRef.current + steps));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      // Only when no control has focus: sliders, radios, buttons and fields keep their own keys.
      if (e.target instanceof Element && e.target.closest(INTERACTIVE)) return;
      const step = { ArrowUp: 1, ArrowDown: -1, PageUp: PAGE, PageDown: -PAGE }[e.key];
      if (step === undefined) return;
      e.preventDefault();
      onSlice(clamp(sliceRef.current + step));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const z = meta.slice_z_mm?.[slice];
  const edges = meta.edges;
  const ratio = dims.cols / dims.rows;
  return (
    <div className="viewer">
      <div className="stage" style={{ ["--ratio" as string]: ratio }}>
        <canvas ref={canvas} width={dims.cols} height={dims.rows} aria-label="Axial CT slice" />
        <div className="corner top-left">
          <span>
            Slice {slice + 1} of {dims.slices}
          </span>
          {z != null && <span>z {z.toFixed(1)} mm</span>}
        </div>
        <div className="corner top-right">
          <span>{win.label}</span>
          <span>
            W {win.width} L {win.center}
          </span>
        </div>
        {edges && (
          <>
            <span className="edge left" title="Patient side">{edges.left}</span>
            <span className="edge right" title="Patient side">{edges.right}</span>
            <span className="edge top" title="Patient side">{edges.top}</span>
            <span className="edge bottom" title="Patient side">{edges.bottom}</span>
          </>
        )}
      </div>
      <div className="scrubber">
        {lanes.length > 0 && <SliceProfile slices={dims.slices} slice={slice} lanes={lanes} onSlice={onSlice} />}
        <input
          className="slice-slider"
          type="range"
          min={0}
          max={dims.slices - 1}
          value={slice}
          onChange={(e) => onSlice(Number(e.target.value))}
          aria-label="Slice"
          aria-valuetext={`Slice ${slice + 1} of ${dims.slices}`}
        />
      </div>
      <div className="toolbar">
        <div className="segmented" role="radiogroup" aria-label="Window">
          {options.map((p) => (
            <label key={p.key}>
              <input type="radio" name="window" value={p.key} checked={p.key === win.key} onChange={() => setPresetKey(p.key)} />
              <span>{p.label}</span>
            </label>
          ))}
        </div>
        <p className="hint">Scroll or press ↑ ↓ to change slice (Page Up/Down moves 10). Click the lesion strip to jump.</p>
      </div>
    </div>
  );
}
