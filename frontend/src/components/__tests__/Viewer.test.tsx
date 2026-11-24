import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Viewer from "../Viewer";

const dims = { slices: 3, rows: 2, cols: 2 };
const meta = {
  shape: [3, 2, 2] as [number, number, number],
  spacing_mm: [1, 0.7, 0.7] as [number, number, number],
  window: { center: 40, width: 400 },
  slice_z_mm: [100, 101, 102.5],
  edges: { left: "R", right: "L", top: "A", bottom: "P" },
};

function setup(slice = 1) {
  const onSlice = vi.fn();
  render(<Viewer volume={new Int16Array(12)} dims={dims} meta={meta} slice={slice} onSlice={onSlice} overlays={[]} lanes={[]} />);
  return onSlice;
}

describe("Viewer", () => {
  it("labels the slice, its position and the patient's sides", () => {
    setup(2);
    expect(screen.getByText("Slice 3 of 3")).toBeInTheDocument();
    expect(screen.getByText("z 102.5 mm")).toBeInTheDocument();
    for (const side of ["R", "L", "A", "P"]) expect(screen.getByText(side)).toBeInTheDocument();
  });

  it("switches window presets", () => {
    setup();
    expect(screen.getByText("W 400 L 40")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Liver" }));
    expect(screen.getByText("W 150 L 30")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Liver" })).toBeChecked();
  });

  it("moves one slice per wheel notch and per arrow key", () => {
    const onSlice = setup(1);
    fireEvent.wheel(screen.getByLabelText("Axial CT slice"), { deltaY: 100 });
    expect(onSlice).toHaveBeenLastCalledWith(2);
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(onSlice).toHaveBeenLastCalledWith(0);
  });

  it("leaves other controls' keys alone", () => {
    const onSlice = vi.fn();
    render(
      <>
        <Viewer volume={new Int16Array(12)} dims={dims} meta={meta} slice={1} onSlice={onSlice} overlays={[]} lanes={[]} />
        <input type="range" aria-label="Fill opacity" />
        <button type="button">Cancel</button>
      </>,
    );
    fireEvent.keyDown(screen.getByRole("slider", { name: "Fill opacity" }), { key: "ArrowUp" });
    fireEvent.keyDown(screen.getByRole("slider", { name: "Fill opacity" }), { key: "PageDown" });
    fireEvent.keyDown(screen.getByRole("button", { name: "Cancel" }), { key: "PageDown" });
    fireEvent.keyDown(screen.getByRole("radio", { name: "Bone" }), { key: "ArrowDown" });
    expect(onSlice).not.toHaveBeenCalled();
  });

  it("does not announce every slice change to screen readers", () => {
    setup();
    expect(screen.getByText("Slice 2 of 3").closest("[aria-live]")).toBeNull();
  });

  it("stays within the series", () => {
    const onSlice = setup(2);
    fireEvent.keyDown(window, { key: "ArrowUp" });
    expect(onSlice).toHaveBeenLastCalledWith(2);
  });
});
