import { BOTH_RGB, REFERENCE_RGB, type RGB } from "./palette";
import type { Overlay } from "./render";

export type ViewMode = "overlay" | "agreement";

export interface ModelLayer {
  mask: Uint8Array | null; // null until downloaded, or if it can't be shown
  rgb: RGB;
  visible: boolean;
  compared: boolean; // the mask the Dice score compares with the reference
}

export interface OverlayInput {
  mode: ViewMode;
  opacity: number;
  models: ModelLayer[];
  reference: { mask: Uint8Array | null; visible: boolean };
}

/**
 * Overlay view: model masks are filled, the reference is outlined on top, so both stay readable where
 * they overlap. Agreement view: the compared model mask and the reference become one layer coloured by
 * model only / both / reference only.
 */
export function buildOverlays({ mode, opacity, models, reference }: OverlayInput): Overlay[] {
  const shown = models.filter((m): m is ModelLayer & { mask: Uint8Array } => m.visible && m.mask !== null);
  const ref = reference.visible ? reference.mask : null;
  const compared = shown.find((m) => m.compared);
  if (mode === "agreement" && compared && ref) {
    return [
      ...shown.filter((m) => m !== compared).map((m) => fill(m, opacity)),
      { kind: "agreement", model: compared.mask, reference: ref, modelOnly: compared.rgb, both: BOTH_RGB, referenceOnly: REFERENCE_RGB, alpha: opacity },
    ];
  }
  const out: Overlay[] = shown.map((m) => fill(m, opacity));
  if (ref) out.push({ kind: "outline", mask: ref, rgb: REFERENCE_RGB });
  return out;
}

const fill = (m: { mask: Uint8Array; rgb: RGB }, alpha: number): Overlay => ({ kind: "fill", mask: m.mask, rgb: m.rgb, alpha });
