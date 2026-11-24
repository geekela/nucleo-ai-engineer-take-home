import type { RGB } from "./palette";

export interface Dims {
  slices: number;
  rows: number;
  cols: number;
}
export interface Window {
  center: number;
  width: number;
}

/** Masks are (slices, rows, cols), values 0/1. */
export type Overlay =
  | { kind: "fill"; mask: Uint8Array; rgb: RGB; alpha: number }
  | { kind: "outline"; mask: Uint8Array; rgb: RGB }
  | { kind: "agreement"; model: Uint8Array; reference: Uint8Array; modelOnly: RGB; both: RGB; referenceOnly: RGB; alpha: number };

/**
 * Draw one axial slice into an RGBA buffer of rows*cols*4.
 * Grey = clamp((HU - (center - width/2)) / width * 255). Overlays are applied in order.
 */
export function renderSlice(ct: Int16Array, dims: Dims, slice: number, win: Window, overlays: Overlay[], out: Uint8ClampedArray): void {
  const { rows, cols } = dims;
  const n = rows * cols;
  const offset = slice * n;
  const low = win.center - win.width / 2;
  const scale = 255 / win.width;
  for (let p = 0; p < n; p++) {
    const g = (ct[offset + p] - low) * scale;
    const grey = g < 0 ? 0 : g > 255 ? 255 : g;
    const q = p * 4;
    out[q] = grey;
    out[q + 1] = grey;
    out[q + 2] = grey;
    out[q + 3] = 255;
  }
  for (const o of overlays) {
    if (o.kind === "fill") {
      for (let p = 0; p < n; p++) if (o.mask[offset + p]) blend(out, p * 4, o.rgb, o.alpha);
    } else if (o.kind === "agreement") {
      for (let p = 0; p < n; p++) {
        const m = o.model[offset + p];
        const r = o.reference[offset + p];
        if (m || r) blend(out, p * 4, m && r ? o.both : m ? o.modelOnly : o.referenceOnly, o.alpha);
      }
    } else {
      for (let p = 0; p < n; p++) {
        if (o.mask[offset + p] && onEdge(o.mask, offset, p, rows, cols)) blend(out, p * 4, o.rgb, 1);
      }
    }
  }
}

function blend(out: Uint8ClampedArray, q: number, rgb: RGB, alpha: number): void {
  out[q] = (1 - alpha) * out[q] + alpha * rgb[0];
  out[q + 1] = (1 - alpha) * out[q + 1] + alpha * rgb[1];
  out[q + 2] = (1 - alpha) * out[q + 2] + alpha * rgb[2];
}

/** A marked pixel is on the edge if any in-slice neighbour (4-connected) is unmarked or off the image. */
function onEdge(mask: Uint8Array, offset: number, p: number, rows: number, cols: number): boolean {
  const r = Math.floor(p / cols);
  const c = p - r * cols;
  return (
    c === 0 || c === cols - 1 || r === 0 || r === rows - 1 ||
    !mask[offset + p - 1] || !mask[offset + p + 1] || !mask[offset + p - cols] || !mask[offset + p + cols]
  );
}
