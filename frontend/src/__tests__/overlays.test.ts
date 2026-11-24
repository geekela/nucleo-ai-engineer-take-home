import { describe, expect, it } from "vitest";
import { buildOverlays, type ModelLayer } from "../overlays";
import { BOTH_RGB, REFERENCE_RGB, type RGB } from "../palette";

const lesions = new Uint8Array([1]);
const liver = new Uint8Array([2]);
const ref = new Uint8Array([3]);
const AMBER: RGB = [1, 1, 1];
const PINK: RGB = [2, 2, 2];

const models = (over: Partial<ModelLayer>[] = []): ModelLayer[] => [
  { mask: liver, rgb: PINK, visible: true, compared: false, ...over[0] },
  { mask: lesions, rgb: AMBER, visible: true, compared: true, ...over[1] },
];

describe("buildOverlays", () => {
  it("fills each visible model mask, then outlines the reference on top", () => {
    const out = buildOverlays({ mode: "overlay", opacity: 0.4, models: models(), reference: { mask: ref, visible: true } });
    expect(out).toEqual([
      { kind: "fill", mask: liver, rgb: PINK, alpha: 0.4 },
      { kind: "fill", mask: lesions, rgb: AMBER, alpha: 0.4 },
      { kind: "outline", mask: ref, rgb: REFERENCE_RGB },
    ]);
  });

  it("skips hidden layers and masks that haven't arrived", () => {
    const out = buildOverlays({
      mode: "overlay",
      opacity: 0.4,
      models: models([{ visible: false }, { mask: null }]),
      reference: { mask: ref, visible: false },
    });
    expect(out).toEqual([]);
  });

  it("in agreement view, replaces the compared mask and the reference outline with one agreement layer", () => {
    const out = buildOverlays({ mode: "agreement", opacity: 0.5, models: models(), reference: { mask: ref, visible: true } });
    expect(out).toEqual([
      { kind: "fill", mask: liver, rgb: PINK, alpha: 0.5 },
      { kind: "agreement", model: lesions, reference: ref, modelOnly: AMBER, both: BOTH_RGB, referenceOnly: REFERENCE_RGB, alpha: 0.5 },
    ]);
  });

  it("falls back to the normal overlay when one side of the comparison is hidden", () => {
    const out = buildOverlays({ mode: "agreement", opacity: 0.5, models: models(), reference: { mask: ref, visible: false } });
    expect(out.map((o) => o.kind)).toEqual(["fill", "fill"]);
  });
});
