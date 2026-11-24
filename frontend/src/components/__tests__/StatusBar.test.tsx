import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StatusBar from "../StatusBar";

const status = { ct: { ok: true, message: null }, reference: { ok: true, message: null }, token_configured: true, base_url: "https://seg.example.org/" };

describe("StatusBar", () => {
  it("names the service the app talks to, and whether a token is set", () => {
    render(<StatusBar status={status} />);
    expect(screen.getByText("seg.example.org")).toBeInTheDocument();
    expect(screen.getByText("Token set")).toBeInTheDocument();
  });

  it("flags a missing token", () => {
    render(<StatusBar status={{ ...status, token_configured: false }} />);
    expect(screen.getByText("No token")).toBeInTheDocument();
  });

  it("credits the data", () => {
    render(<StatusBar status={null} />);
    expect(screen.getByRole("link", { name: /10.5281\/zenodo.20272572/ })).toBeInTheDocument();
  });
});
