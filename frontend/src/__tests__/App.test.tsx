import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { run } from "../components/__tests__/fixtures";
import type { Overlay } from "../render";

// Record what the viewer draws, so tests can check which upload's mask is on screen.
const drawn = vi.hoisted(() => ({ overlays: [] as Overlay[][] }));
vi.mock("../render", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../render")>();
  return {
    ...actual,
    renderSlice: (...args: Parameters<typeof actual.renderSlice>) => {
      drawn.overlays.push(args[4]);
      return actual.renderSlice(...args);
    },
  };
});

/** Give the viewer a canvas context so it actually draws (jsdom has none). */
function withCanvas() {
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = (() => ({
    createImageData: (w: number, h: number) => ({ width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }),
    putImageData: () => {},
  })) as unknown as typeof original;
  return () => {
    HTMLCanvasElement.prototype.getContext = original;
  };
}

const lastFillMasks = () => (drawn.overlays.at(-1) ?? []).filter((o) => o.kind === "fill").map((o) => (o.kind === "fill" ? Array.from(o.mask) : []));
const maskWith = (i: number) => {
  const m = new Uint8Array(8);
  m[i] = 1;
  return m;
};
const lesions = (compared = true) => ({ name: "lesions", overlay_ok: true, message: null, compared });

type Handler = (init?: RequestInit) => Response | Promise<Response>;

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const META = { shape: [2, 2, 2], spacing_mm: [1, 1, 1], window: { center: 40, width: 400 }, slice_z_mm: [0, 1], edges: { left: "R", right: "L", top: "A", bottom: "P" } };
const STATUS = { ct: { ok: true, message: null }, reference: { ok: true, message: null }, token_configured: true, base_url: "https://seg.example.org" };

/** A stand-in for the local backend: route -> handler. Unknown routes fail the test loudly. */
function backend(routes: Record<string, Handler>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const key = `${init?.method ?? "GET"} ${url}`;
    const handler = routes[key];
    if (!handler) throw new Error(`unexpected request ${key}`);
    return handler(init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

const base: Record<string, Handler> = {
  "GET /api/status": () => json(STATUS),
  "GET /api/ct": () => json(META),
  "GET /api/ct/volume": () => new Response(new Int16Array(8).buffer),
  "GET /api/reference/mask": () => new Response(new Uint8Array(8)),
  "GET /api/runs": () => json([]),
};

afterEach(() => {
  vi.unstubAllGlobals();
  drawn.overlays.length = 0;
});

describe("App", () => {
  it("offers to try again when the CT fails to load, and then shows it", async () => {
    let attempts = 0;
    backend({
      ...base,
      "GET /api/ct/volume": () => (++attempts === 1 ? json({ message: "The CT couldn't be sent." }, 500) : new Response(new Int16Array(8).buffer)),
    });
    render(<App />);
    expect(await screen.findByText("The CT couldn't be sent.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Axial CT slice")).toBeInTheDocument();
  });

  it("shows a finished upload's Dice score and a switch for each structure it returned", async () => {
    const done = run({
      run_id: "r1",
      dice: { value: 0.9123, message: null },
      structures: [
        { name: "lesions", overlay_ok: true, message: null, compared: true },
        { name: "liver", overlay_ok: true, message: null, compared: false },
      ],
    });
    const calls = backend({
      ...base,
      "GET /api/runs": () => json([done]),
      "GET /api/runs/r1/structures/0": () => new Response(new Uint8Array(8)),
      "GET /api/runs/r1/structures/1": () => new Response(new Uint8Array(8)),
    });
    render(<App />);
    expect(await screen.findByText("0.912")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Model: lesions" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Model: liver" })).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.url === "/api/runs/r1/structures/1")).toBe(true));
  });

  it("cancels an upload in progress", async () => {
    const busy = run({ run_id: "r2", status: "uploading", status_since: Date.now() / 1000 });
    let cancelled = false;
    backend({
      ...base,
      "GET /api/runs": () => json([cancelled ? { ...busy, status: "cancelled", message: "Upload cancelled." } : busy]),
      "POST /api/runs/r2/cancel": () => {
        cancelled = true;
        return json({ ...busy, status: "cancelled", message: "Upload cancelled." });
      },
    });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Cancel upload/ }));
    expect(await screen.findByText("Upload cancelled.")).toBeInTheDocument();
  });

  it("explains that a dropped folder can't be uploaded, and sends nothing", async () => {
    const calls = backend(base);
    render(<App />);
    await screen.findByLabelText("Axial CT slice");
    const folder = new File([], "case-001");
    fireEvent.drop(window, {
      dataTransfer: {
        types: ["Files"],
        files: [folder],
        items: [{ kind: "file", webkitGetAsEntry: () => ({ isDirectory: true, name: "case-001" }) }],
      },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("case-001 is a folder. Upload the zip file instead.");
    expect(calls.some((c) => c.init?.method === "POST")).toBe(false);
  });

  it("shows a new result when it finishes, keeps your choice, and falls back when an upload is cleared", async () => {
    const a = run({ run_id: "a", source: "a.zip", created_at: 1, dice: { value: 0.5, message: null } });
    const b = run({ run_id: "b", source: "b.zip", created_at: 2, dice: { value: 0.6, message: null } });
    const busy = run({ run_id: "c", source: "c.zip", status: "uploading", created_at: 3 }); // keeps polling fast
    let list = [busy, a];
    backend({ ...base, "GET /api/runs": () => json(list) });
    render(<App />);
    expect(await screen.findByText(/^From a\.zip/)).toBeInTheDocument();
    list = [busy, b, a];
    expect(await screen.findByText(/^From b\.zip/, {}, { timeout: 3000 })).toBeInTheDocument(); // a fresh result is shown
    fireEvent.click(screen.getByRole("button", { name: /^Show a\.zip/ }));
    expect(screen.getByText(/^From a\.zip/)).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 1200)); // another poll with the same list
    expect(screen.getByText(/^From a\.zip/)).toBeInTheDocument(); // your choice stays
    list = [busy, b, { ...a, cleared: true }];
    expect(await screen.findByText(/^From b\.zip/, {}, { timeout: 3000 })).toBeInTheDocument();
  }, 10_000);

  it("draws the selected upload's mask, never the previous one's", async () => {
    const restore = withCanvas();
    const a = run({ run_id: "a", source: "a.zip", created_at: 1, structures: [lesions()] });
    const b = run({ run_id: "b", source: "b.zip", created_at: 2, structures: [lesions()] });
    backend({
      ...base,
      "GET /api/runs": () => json([b, a]),
      "GET /api/runs/a/structures/0": () => new Response(maskWith(0)),
      "GET /api/runs/b/structures/0": () => new Response(maskWith(7)),
    });
    render(<App />);
    await waitFor(() => expect(lastFillMasks()).toEqual([Array.from(maskWith(7))]));
    fireEvent.click(await screen.findByRole("button", { name: /^Show a\.zip/ }));
    await waitFor(() => expect(lastFillMasks()).toEqual([Array.from(maskWith(0))]));
    fireEvent.click(screen.getByRole("button", { name: /^Show b\.zip/ })); // b's mask is already cached
    await waitFor(() => expect(lastFillMasks()).toEqual([Array.from(maskWith(7))]));
    restore();
  });

  it("goes back to the overlay view when the shown upload has nothing to compare", async () => {
    const a = run({ run_id: "a", source: "a.zip", created_at: 1, structures: [lesions()] });
    const b = run({ run_id: "b", source: "b.zip", created_at: 2, structures: [{ name: "spleen", overlay_ok: true, message: null, compared: false }] });
    backend({
      ...base,
      "GET /api/runs": () => json([b, a]),
      "GET /api/runs/a/structures/0": () => new Response(maskWith(0)),
      "GET /api/runs/b/structures/0": () => new Response(maskWith(7)),
    });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /^Show a\.zip/ }));
    await waitFor(() => expect(screen.getByRole("radio", { name: "Agreement" })).toBeEnabled());
    fireEvent.click(screen.getByRole("radio", { name: "Agreement" }));
    fireEvent.click(screen.getByRole("button", { name: /^Show b\.zip/ }));
    await waitFor(() => expect(screen.getByRole("radio", { name: "Overlay" })).toBeChecked());
    expect(screen.getByRole("radio", { name: "Agreement" })).toBeDisabled();
  });

  it("colours the breakdown bar like the compared mask, even before it has downloaded", async () => {
    const r = run({
      run_id: "r",
      dice: { value: 0.9, message: null, voxels_model: 10, voxels_reference: 10, voxels_overlap: 9, ml_model: 1, ml_reference: 1 },
      structures: [{ name: "liver", overlay_ok: true, message: null, compared: false }, lesions()],
    });
    backend({
      ...base,
      "GET /api/runs": () => json([r]),
      "GET /api/runs/r/structures/0": () => new Promise<Response>(() => {}),
      "GET /api/runs/r/structures/1": () => new Promise<Response>(() => {}),
    });
    render(<App />);
    const label = await screen.findByText("Model only");
    expect((label.previousElementSibling as HTMLElement).style.color).toBe("rgb(226, 109, 196)"); // lesions is structure 1
  });

  it("lets you retry a mask that failed to download", async () => {
    let attempts = 0;
    const r = run({ run_id: "r", structures: [lesions()] });
    backend({
      ...base,
      "GET /api/runs": () => json([r]),
      "GET /api/runs/r/structures/0": () => (++attempts === 1 ? json({ message: "The mask couldn't be sent." }, 503) : new Response(maskWith(0))),
    });
    render(<App />);
    const layers = within(await screen.findByRole("region", { name: "Layers" }));
    expect(await layers.findByText("The mask couldn't be sent.")).toBeInTheDocument();
    fireEvent.click(layers.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(layers.queryByText("The mask couldn't be sent.")).not.toBeInTheDocument());
    expect(layers.getByRole("switch", { name: "Model: lesions" })).toBeEnabled();
    expect(attempts).toBe(2);
  });

  it("offers to try again when the local server answers the first check with an error", async () => {
    let attempts = 0;
    backend({
      ...base,
      "GET /api/status": () => (++attempts === 1 ? json({ message: "The local server hit an unexpected error." }, 500) : json(STATUS)),
    });
    render(<App />);
    expect(await screen.findByText("The local server hit an unexpected error.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Axial CT slice")).toBeInTheDocument();
  });

  it("says so when the local server can't be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<App />);
    expect(await screen.findByText(/Lost connection to the local server/)).toBeInTheDocument();
  });
});
