import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Layers, { type Layer } from "../Layers";

const layers: Layer[] = [
  { key: "m0", label: "Model: lesions", rgb: [255, 158, 61], style: "fill", available: true, checked: true, note: null },
  { key: "ref", label: "Reference", rgb: [92, 184, 240], style: "outline", available: false, checked: true, note: "Reference mask not found." },
];

function setup(over = {}) {
  const props = { layers, onToggle: vi.fn(), mode: "overlay" as const, onMode: vi.fn(), canCompare: true, opacity: 0.45, onOpacity: vi.fn(), ...over };
  render(<Layers {...props} />);
  return props;
}

describe("Layers", () => {
  it("has one switch per layer, and explains one that can't be shown", () => {
    const p = setup();
    fireEvent.click(screen.getByRole("switch", { name: "Model: lesions" }));
    expect(p.onToggle).toHaveBeenCalledWith("m0");
    const ref = screen.getByRole("switch", { name: "Reference" });
    expect(ref).toBeDisabled();
    expect(screen.getByText("Reference mask not found.")).toBeInTheDocument();
  });

  it("switches between the overlay and agreement views", () => {
    const p = setup();
    fireEvent.click(screen.getByRole("radio", { name: "Agreement" }));
    expect(p.onMode).toHaveBeenCalledWith("agreement");
  });

  it("offers the agreement view only when there is something to compare", () => {
    setup({ canCompare: false });
    expect(screen.getByRole("radio", { name: "Agreement" })).toBeDisabled();
  });

  it("changes the fill opacity", () => {
    const p = setup();
    fireEvent.change(screen.getByRole("slider", { name: "Fill opacity" }), { target: { value: "70" } });
    expect(p.onOpacity).toHaveBeenCalledWith(0.7);
  });
});
