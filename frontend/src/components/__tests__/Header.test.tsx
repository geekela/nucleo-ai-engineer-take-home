import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Header from "../Header";


describe("Header", () => {
  it("uploads every file you choose", () => {
    const onUploadFiles = vi.fn();
    render(<Header meta={null} canUploadBundled onUploadBundled={() => {}} onUploadFiles={onUploadFiles} />);
    const a = new File(["a"], "a.zip");
    const b = new File(["b"], "b.zip");
    fireEvent.change(screen.getByLabelText("Choose zip files to upload"), { target: { files: [a, b] } });
    expect(onUploadFiles).toHaveBeenCalledWith([a, b]);
  });

  it("sends the bundled series", () => {
    const onUploadBundled = vi.fn();
    render(<Header meta={null} canUploadBundled onUploadBundled={onUploadBundled} onUploadFiles={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Upload case-001" }));
    expect(onUploadBundled).toHaveBeenCalled();
  });
});
