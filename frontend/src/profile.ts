import type { Dims } from "./render";

/** Marked voxels on each axial slice of a (slices, rows, cols) mask. */
export function sliceCounts(mask: Uint8Array, dims: Dims): Uint32Array {
  const per = dims.rows * dims.cols;
  const counts = new Uint32Array(dims.slices);
  for (let s = 0; s < dims.slices; s++) {
    let n = 0;
    const end = (s + 1) * per;
    for (let i = s * per; i < end; i++) n += mask[i] ? 1 : 0;
    counts[s] = n;
  }
  return counts;
}
