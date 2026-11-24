import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ComparePanel from "../ComparePanel";
import { run } from "./fixtures";

const dice = {
  value: 0.9284,
  message: null,
  voxels_model: 1000,
  voxels_reference: 1100,
  voxels_overlap: 975,
  ml_model: 386.94,
  ml_reference: 398.71,
  shape: [416, 350, 180] as [number, number, number],
  lesions: { reference: 10, detected: 9, model: 11, model_only: 2 },
};

describe("ComparePanel", () => {
  it("says what to do before there is any result", () => {
    render(<ComparePanel run={null} />);
    expect(screen.getByText(/No model result yet/)).toBeInTheDocument();
  });

  it("names the upload whose result it shows", () => {
    render(<ComparePanel run={run({ dice, source: "scan-b.zip" })} />);
    expect(screen.getByText(/^From scan-b\.zip, uploaded /)).toBeInTheDocument();
  });

  it("shows the Dice score, the volumes and the grid it was computed on", () => {
    render(<ComparePanel run={run({ dice })} />);
    expect(screen.getByText("0.928")).toBeInTheDocument();
    expect(screen.getByText("Grid check passed: 416 × 350 × 180 voxels, same affine.")).toBeInTheDocument();
    expect(screen.getByText("386.9 mL")).toBeInTheDocument();
    expect(screen.getByText("398.7 mL")).toBeInTheDocument();
  });

  it("splits the volumes into both, model only and reference only", () => {
    render(<ComparePanel run={run({ dice })} />);
    // 975 shared voxels of 1000 (model) and 1100 (reference)
    expect(screen.getByRole("img", { name: "975 voxels in both, 25 in the model only, 125 in the reference only" })).toBeInTheDocument();
  });

  it("counts lesions the model found and missed", () => {
    render(<ComparePanel run={run({ dice })} />);
    expect(screen.getByText("9 of 10")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("gives the reason instead of a number when Dice can't be computed", () => {
    render(<ComparePanel run={run({ dice: { value: null, message: "The masks are positioned differently in space." } })} />);
    expect(screen.getByText("The masks are positioned differently in space.")).toBeInTheDocument();
    expect(screen.queryByText(/Grid check passed/)).not.toBeInTheDocument();
  });

  it("explains an upload the service matched to another case", () => {
    render(<ComparePanel run={run({ warning: "The service identified this upload as case-999." })} />);
    expect(screen.getByText("The service identified this upload as case-999.")).toBeInTheDocument();
  });
});
