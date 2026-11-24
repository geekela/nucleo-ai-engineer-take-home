import { afterEach, describe, expect, it, vi } from "vitest";
import { cancelRun, clearFailedRuns, getStructureMask, listRuns, startFileRun, UNRESPONSIVE_MESSAGE } from "../api";

afterEach(() => vi.unstubAllGlobals());

describe("api", () => {
  it("gives up on a hung local server instead of waiting forever", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("timed out", "TimeoutError")));
    })));
    const pending = listRuns();
    await vi.advanceTimersByTimeAsync(15_000);
    expect(await pending).toEqual({ ok: false, offline: true, message: UNRESPONSIVE_MESSAGE });
    vi.useRealTimers();
  });

  it("reports a lost local server instead of throwing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const r = await listRuns();
    expect(r).toEqual({ ok: false, offline: true, message: "Lost connection to the local server. Is it still running?" });
  });

  it("passes the server's plain message through on an error status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ message: "No such upload." }), { status: 404 })));
    const r = await getStructureMask("abc", 0);
    expect(r).toEqual({ ok: false, offline: false, message: "No such upload." });
  });

  it("treats a 5xx without our JSON message as the local server being unreachable (Vite proxy error)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 500 })));
    expect(await listRuns()).toEqual({ ok: false, offline: true, message: "Lost connection to the local server. Is it still running?" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>", { status: 502 })));
    expect(await listRuns()).toMatchObject({ ok: false, offline: true });
  });

  it("keeps the backend's own 500 message and does not call it offline", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ message: "The local server hit an unexpected error." }), { status: 500 })));
    expect(await listRuns()).toEqual({ ok: false, offline: false, message: "The local server hit an unexpected error." });
  });

  it("falls back to a generic message for a 4xx that isn't JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nope", { status: 404 })));
    expect(await listRuns()).toEqual({ ok: false, offline: false, message: "The local server returned an error." });
  });

  it("returns binary masks as Uint8Array, fetched by structure index", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Uint8Array([0, 1, 1])));
    vi.stubGlobal("fetch", fetchMock);
    const r = await getStructureMask("abc", 2);
    expect(r.ok && Array.from(r.value)).toEqual([0, 1, 1]);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/runs/abc/structures/2");
  });

  it("uploads a file as raw zip bytes, URL-encoding the run path", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ run_id: "r1" })));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File([new Uint8Array([80, 75])], "scan.zip");
    await startFileRun(file);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/runs");
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/zip");
    expect(init.headers["X-Filename"]).toBe("scan.zip");
    expect(init.body).toBe(file);
  });
});

describe("api: cancelling and local checks", () => {
  it("cancels an upload by run id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ run_id: "a b", status: "cancelled" })));
    vi.stubGlobal("fetch", fetchMock);
    const r = await cancelRun("a b");
    expect(r.ok && r.value.status).toBe("cancelled");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/runs/a%20b/cancel");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  });

  it("clears failed uploads", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ removed: 3 })));
    vi.stubGlobal("fetch", fetchMock);
    const r = await clearFailedRuns();
    expect(r.ok && r.value.removed).toBe(3);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/runs/clear-failed");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  });

  it("says so plainly when the browser can't read a file, instead of blaming the local server", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const unreadable = {
      name: "scans",
      size: 96,
      slice: () => ({ arrayBuffer: () => Promise.reject(new DOMException("A requested file could not be found", "NotFoundError")) }),
    } as unknown as File;
    const r = await startFileRun(unreadable);
    expect(r).toEqual({ ok: false, offline: false, message: "Couldn't read scans. If it's a folder, choose the zip file inside it." });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a file over the service's 150 MB limit without sending it", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const big = { name: "big.zip", size: 157_286_401 } as File;
    const r = await startFileRun(big);
    expect(r).toEqual({ ok: false, offline: false, message: "big.zip is larger than the 150 MB the service accepts, so it wasn't uploaded." });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
