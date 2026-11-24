export type RGB = [number, number, number];

/** Colour is kept for the masks. Each hue on screen means one thing. */

/** The reference (ground truth). Drawn as an outline; no model structure uses it. */
export const REFERENCE_RGB: RGB = [92, 184, 240]; // sky

/** Voxels both the model and the reference mark (agreement view). */
export const BOTH_RGB: RGB = [31, 184, 154]; // teal

const MODEL_PALETTE: RGB[] = [
  [255, 158, 61], // amber
  [226, 109, 196], // orchid
  [240, 228, 66], // yellow
  [213, 94, 0], // vermilion
  [170, 200, 80], // lime
  [190, 150, 255], // lavender
];

export function modelColour(index: number): RGB {
  return MODEL_PALETTE[((index % MODEL_PALETTE.length) + MODEL_PALETTE.length) % MODEL_PALETTE.length];
}

export const rgbCss = ([r, g, b]: RGB) => `rgb(${r}, ${g}, ${b})`;
