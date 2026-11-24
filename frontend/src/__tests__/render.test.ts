import { describe, expect, it } from "vitest";
import { renderSlice } from "../render";
import { BOTH_RGB, REFERENCE_RGB, modelColour, type RGB } from "../palette";

const W = { center: 40, width: 400 };

function one(hu: number, overlays: Parameters<typeof renderSlice>[4] = []) {
  const out = new Uint8ClampedArray(4);
  renderSlice(new Int16Array([hu]), { slices: 1, rows: 1, cols: 1 }, 0, W, overlays, out);
  return Array.from(out);
}

describe("renderSlice", () => {
  it("maps the soft-tissue window to grey", () => {
    expect(one(-160)).toEqual([0, 0, 0, 255]);
    expect(one(240)).toEqual([255, 255, 255, 255]);
    expect(one(-1000)[0]).toBe(0);
    expect(one(3000)[0]).toBe(255);
    expect(Math.abs(one(40)[0] - 128)).toBeLessThanOrEqual(1);
  });

  it("paints an opaque overlay in its colour, and leaves unmasked pixels grey", () => {
    expect(one(-160, [{ kind: "fill", mask: new Uint8Array([1]), rgb: [255, 0, 0], alpha: 1 }])).toEqual([255, 0, 0, 255]);
    expect(one(-160, [{ kind: "fill", mask: new Uint8Array([0]), rgb: [255, 0, 0], alpha: 1 }])).toEqual([0, 0, 0, 255]);
  });

  it("blends a translucent overlay", () => {
    const [r, g] = one(-160, [{ kind: "fill", mask: new Uint8Array([1]), rgb: [200, 0, 0], alpha: 0.5 }]);
    expect(r).toBe(100);
    expect(g).toBe(0);
  });

  it("reads the requested slice of a (slices, rows, cols) volume", () => {
    // dims 2 slices x 1 row x 2 cols; slice 1 is [240, -160]
    const ct = new Int16Array([-160, -160, 240, -160]);
    const mask = new Uint8Array([0, 0, 0, 1]);
    const out = new Uint8ClampedArray(8);
    renderSlice(ct, { slices: 2, rows: 1, cols: 2 }, 1, W, [{ kind: "fill", mask, rgb: [0, 0, 255], alpha: 1 }], out);
    expect(Array.from(out)).toEqual([255, 255, 255, 255, 0, 0, 255, 255]);
  });
});

describe("outline overlays", () => {
  // 5x5 slice, CT black everywhere, a 3x3 mask block in the middle.
  const dims = { slices: 1, rows: 5, cols: 5 };
  const ct = new Int16Array(25).fill(-1000);
  const block = new Uint8Array(25);
  for (const r of [1, 2, 3]) for (const c of [1, 2, 3]) block[r * 5 + c] = 1;

  it("draws only the edge of a mask, at full strength, and leaves its inside alone", () => {
    const out = new Uint8ClampedArray(100);
    renderSlice(ct, dims, 0, W, [{ kind: "outline", mask: block, rgb: [0, 200, 255] }], out);
    const painted = (r: number, c: number) => out[(r * 5 + c) * 4 + 2] === 255;
    const edge = [[1, 1], [1, 2], [1, 3], [2, 1], [2, 3], [3, 1], [3, 2], [3, 3]];
    for (const [r, c] of edge) expect(painted(r, c)).toBe(true);
    expect(painted(2, 2)).toBe(false); // inside
    expect(painted(0, 0)).toBe(false); // outside
    expect(Array.from(out.slice((1 * 5 + 1) * 4, (1 * 5 + 1) * 4 + 4))).toEqual([0, 200, 255, 255]);
  });

  it("treats the image border as an edge", () => {
    const out = new Uint8ClampedArray(4);
    renderSlice(new Int16Array([-1000]), { slices: 1, rows: 1, cols: 1 }, 0, W, [{ kind: "outline", mask: new Uint8Array([1]), rgb: [9, 9, 9] }], out);
    expect(Array.from(out)).toEqual([9, 9, 9, 255]);
  });
});

describe("agreement overlay", () => {
  it("colours voxels by who marked them: model only, both, reference only", () => {
    const out = new Uint8ClampedArray(16);
    const colours = { modelOnly: [255, 0, 0] as RGB, both: [0, 255, 0] as RGB, referenceOnly: [0, 0, 255] as RGB };
    renderSlice(
      new Int16Array([-1000, -1000, -1000, -1000]),
      { slices: 1, rows: 1, cols: 4 },
      0,
      W,
      [{ kind: "agreement", model: new Uint8Array([1, 1, 0, 0]), reference: new Uint8Array([0, 1, 1, 0]), ...colours, alpha: 1 }],
      out,
    );
    expect(Array.from(out)).toEqual([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 0, 0, 0, 255]);
  });
});

describe("palette", () => {
  it("never gives a model structure the reference or the agreement colour", () => {
    for (let i = 0; i < 21; i++) {
      expect(modelColour(i)).not.toEqual(REFERENCE_RGB);
      expect(modelColour(i)).not.toEqual(BOTH_RGB);
    }
  });
});
