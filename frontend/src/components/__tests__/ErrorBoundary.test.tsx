import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ErrorBoundary from "../ErrorBoundary";

function Broken(): JSX.Element {
  throw new Error("render bug");
}

describe("ErrorBoundary", () => {
  it("replaces a crashed page with a plain message and a way back", () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Broken />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("your uploads are kept by the local server");
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
    expect(screen.queryByText("render bug")).not.toBeInTheDocument();
    quiet.mockRestore();
  });
});
