import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import SliceProfile from "../SliceProfile";

describe("SliceProfile", () => {
  const lanes = [
    { label: "Model", rgb: [255, 158, 61] as [number, number, number], counts: new Uint32Array([0, 4, 9, 0]) },
    { label: "Reference", rgb: [92, 184, 240] as [number, number, number], counts: new Uint32Array([0, 0, 3, 3]) },
  ];

  it("marks the slices that have lesion voxels, lane by lane", () => {
    const { container } = render(<SliceProfile slices={4} slice={0} lanes={lanes} onSlice={() => {}} />);
    expect(container.querySelectorAll('[data-lane="Model"] rect')).toHaveLength(2);
    expect(container.querySelectorAll('[data-lane="Reference"] rect')).toHaveLength(2);
    expect(screen.getByText("Model")).toBeInTheDocument();
  });

  it("draws bar heights on one shared scale, so the lanes can be compared", () => {
    const { container } = render(<SliceProfile slices={4} slice={0} lanes={lanes} onSlice={() => {}} />);
    const h = (lane: string, i: number) => Number(container.querySelectorAll(`[data-lane="${lane}"] rect`)[i].getAttribute("height"));
    // Model has 4 and 9 voxels; the reference 3 and 3. The largest count anywhere (9) is a full bar.
    expect(h("Model", 0) / h("Model", 1)).toBeCloseTo(4 / 9);
    expect(h("Model", 1) / h("Reference", 0)).toBeCloseTo(3);
  });

  it("jumps to the slice you click", () => {
    const onSlice = vi.fn();
    render(<SliceProfile slices={4} slice={0} lanes={lanes} onSlice={onSlice} />);
    const strip = screen.getByTestId("slice-profile");
    strip.getBoundingClientRect = () => ({ left: 100, width: 400, top: 0, height: 30, right: 500, bottom: 30, x: 100, y: 0, toJSON() {} });
    fireEvent.pointerDown(strip, { clientX: 100 + 250, pointerId: 1 }); // 62.5% of the way -> slice 2 of 0..3
    expect(onSlice).toHaveBeenCalledWith(2);
  });
});
