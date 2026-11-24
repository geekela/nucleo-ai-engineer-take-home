import { describe, expect, it } from "vitest";
import { sliceCounts } from "../profile";

describe("sliceCounts", () => {
  it("counts the marked voxels on each slice", () => {
    // 3 slices of 1 x 2 voxels
    const mask = new Uint8Array([1, 0, 0, 0, 1, 1]);
    expect(Array.from(sliceCounts(mask, { slices: 3, rows: 1, cols: 2 }))).toEqual([1, 0, 2]);
  });
});
