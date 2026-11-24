import { describe, expect, it } from "vitest";
import { wheelStep } from "../wheel";

describe("wheelStep", () => {
  it("moves one slice per mouse-wheel notch", () => {
    expect(wheelStep(0, 100)).toEqual({ steps: 1, acc: 0 });
    expect(wheelStep(0, -120)).toEqual({ steps: -1, acc: 0 });
  });

  it("adds up small trackpad movements before moving", () => {
    let s = wheelStep(0, 10);
    expect(s.steps).toBe(0);
    s = wheelStep(s.acc, 10);
    expect(s.steps).toBe(0);
    s = wheelStep(s.acc, 10); // 30 px in total
    expect(s.steps).toBe(1);
    expect(s.acc).toBe(6);
  });

  it("starts again when the direction changes", () => {
    expect(wheelStep(20, -10)).toEqual({ steps: 0, acc: -10 });
  });
});
