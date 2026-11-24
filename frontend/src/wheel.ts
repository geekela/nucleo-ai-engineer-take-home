/** A mouse-wheel notch is a large delta; a trackpad sends many small ones. */
const NOTCH_PX = 50;
const TRACKPAD_STEP_PX = 24;

/**
 * Turn one wheel event into whole-slice steps. `acc` carries leftover trackpad movement
 * between events, so a slow swipe still moves one slice at a time.
 */
export function wheelStep(acc: number, deltaY: number): { steps: number; acc: number } {
  if (Math.abs(deltaY) >= NOTCH_PX) return { steps: Math.sign(deltaY), acc: 0 };
  const total = Math.sign(acc) === -Math.sign(deltaY) ? deltaY : acc + deltaY;
  const steps = Math.trunc(total / TRACKPAD_STEP_PX) || 0; // no -0
  return { steps, acc: total - steps * TRACKPAD_STEP_PX };
}
