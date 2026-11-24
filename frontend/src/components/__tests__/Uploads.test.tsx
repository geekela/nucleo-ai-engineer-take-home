import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Uploads from "../Uploads";
import { run } from "./fixtures";

const noop = () => {};

describe("Uploads", () => {
  it("invites a first upload when there are none", () => {
    render(<Uploads runs={[]} selected={null} now={0} notices={[]} onSelect={noop} onCancel={noop} onDismiss={noop} onClearFailed={noop} />);
    expect(screen.getByText(/No uploads yet/)).toBeInTheDocument();
  });

  it("lets you cancel an upload that is still running, and only then", () => {
    const onCancel = vi.fn();
    const runs = [run({ run_id: "a", status: "segmenting", status_since: 990 }), run({ run_id: "b", status: "done" })];
    render(<Uploads runs={runs} selected={null} now={994.5} notices={[]} onSelect={noop} onCancel={onCancel} onDismiss={noop} onClearFailed={noop} />);
    expect(screen.getByText("Segmenting, 4 s")).toBeInTheDocument();
    const buttons = screen.getAllByRole("button", { name: /Cancel upload/ });
    expect(buttons).toHaveLength(1);
    fireEvent.click(buttons[0]);
    expect(onCancel).toHaveBeenCalledWith("a");
  });

  it("shows a progress bar while the zip is being sent", () => {
    const runs = [run({ status: "uploading", upload_fraction: 0.25 })];
    render(<Uploads runs={runs} selected={null} now={0} notices={[]} onSelect={noop} onCancel={noop} onDismiss={noop} onClearFailed={noop} />);
    expect(screen.getByRole("progressbar", { name: /Sending case-001-dicom.zip/ })).toHaveAttribute("aria-valuenow", "25");
  });

  it("counts down while the service asks us to wait", () => {
    const runs = [run({ status: "waiting_to_retry", retry_at: 1_030 })];
    render(<Uploads runs={runs} selected={null} now={1_018} notices={[]} onSelect={noop} onCancel={noop} onDismiss={noop} onClearFailed={noop} />);
    expect(screen.getByText("Service busy. Retrying in 12 s")).toBeInTheDocument();
  });

  it("explains a failure in plain words, with the technical detail tucked away", () => {
    const runs = [
      run({ status: "failed", source: "photo.png", message: "That file isn't a valid zip archive.", detail: "HTTP 400 · bad_request · not_zip · 1 attempt" }),
    ];
    render(<Uploads runs={runs} selected={null} now={0} notices={[]} onSelect={noop} onCancel={noop} onDismiss={noop} onClearFailed={noop} />);
    expect(screen.getByText("That file isn't a valid zip archive.")).toBeInTheDocument();
    const details = screen.getByText("Technical details").closest("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText(/not_zip/)).toBeInTheDocument();
  });

  it("selects a finished upload, but not a failed one", () => {
    const onSelect = vi.fn();
    const runs = [
      run({ run_id: "good", dice: { value: 0.9284, message: null } }),
      run({ run_id: "bad", status: "failed", message: "Nope." }),
    ];
    render(<Uploads runs={runs} selected="good" now={0} notices={[]} onSelect={onSelect} onCancel={noop} onDismiss={noop} onClearFailed={noop} />);
    const good = screen.getByRole("button", { name: /Show case-001-dicom.zip/ });
    expect(good).toHaveAttribute("aria-pressed", "true");
    expect(within(good).getByText("DSC 0.928")).toBeInTheDocument();
    fireEvent.click(good);
    expect(onSelect).toHaveBeenCalledWith("good");
    expect(screen.getAllByRole("button", { name: /Show / })).toHaveLength(1);
  });

  it("clears failed and cancelled uploads in one go, when there are any", () => {
    const onClearFailed = vi.fn();
    const props = { selected: null, now: 0, notices: [], onSelect: noop, onCancel: noop, onDismiss: noop, onClearFailed };
    const { rerender } = render(<Uploads {...props} runs={[run({ status: "done" })]} />);
    expect(screen.queryByRole("button", { name: "Clear failed" })).not.toBeInTheDocument();
    rerender(<Uploads {...props} runs={[run({ status: "done" }), run({ run_id: "x", status: "cancelled" })]} />);
    fireEvent.click(screen.getByRole("button", { name: "Clear failed" }));
    expect(onClearFailed).toHaveBeenCalled();
  });

  it("shows notices about files that were never sent, and lets you dismiss them", () => {
    const onDismiss = vi.fn();
    render(
      <Uploads runs={[]} selected={null} now={0} notices={["big.zip is too large."]} onSelect={noop} onCancel={noop} onDismiss={onDismiss} onClearFailed={noop} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("big.zip is too large.");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalledWith(0);
  });
});
