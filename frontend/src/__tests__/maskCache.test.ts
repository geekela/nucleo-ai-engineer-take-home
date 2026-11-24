import { describe, expect, it, vi } from "vitest";
import type { Result } from "../api";
import { MaskCache } from "../maskCache";

function deferred() {
  let resolve!: (r: Result<Uint8Array>) => void;
  const promise = new Promise<Result<Uint8Array>>((r) => (resolve = r));
  return { promise, resolve };
}

describe("MaskCache", () => {
  it("fetches each mask once and keeps it per upload and structure", async () => {
    const onChange = vi.fn();
    const cache = new MaskCache(onChange);
    const mask = new Uint8Array([1]);
    const fetcher = vi.fn().mockResolvedValue({ ok: true, value: mask });
    cache.request("run-a", 0, fetcher);
    cache.request("run-a", 0, fetcher);
    expect(cache.get("run-a", 0)).toEqual({ state: "loading" });
    await vi.waitFor(() => expect(cache.get("run-a", 0)).toEqual({ state: "ok", mask }));
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(cache.get("run-b", 0)).toBeUndefined();
    expect(onChange).toHaveBeenCalled();
  });

  it("keeps the plain error when a download fails", async () => {
    const cache = new MaskCache(() => {});
    cache.request("run-a", 1, () => Promise.resolve({ ok: false, offline: false, message: "This upload has no such mask." }));
    await vi.waitFor(() => expect(cache.get("run-a", 1)).toEqual({ state: "error", message: "This upload has no such mask." }));
  });

  it("drops masks of cleared uploads, even one that was still downloading", async () => {
    const cache = new MaskCache(() => {});
    const late = deferred();
    cache.request("old", 0, () => late.promise);
    cache.request("new", 0, () => Promise.resolve({ ok: true, value: new Uint8Array([1]) }));
    cache.retain(new Set(["new"]));
    late.resolve({ ok: true, value: new Uint8Array([7]) });
    await late.promise;
    await Promise.resolve();
    expect(cache.get("old", 0)).toBeUndefined();
    expect(cache.get("new", 0)).toBeDefined();
  });
});

describe("MaskCache memory", () => {
  const ok = (v: number) => () => Promise.resolve({ ok: true as const, value: new Uint8Array([v]) });

  it("keeps the masks of the most recently viewed uploads only", async () => {
    const cache = new MaskCache(() => {}, 2);
    cache.request("a", 0, ok(1));
    cache.request("b", 0, ok(2));
    cache.request("c", 0, ok(3)); // a is the least recently viewed
    expect(cache.get("a", 0)).toBeUndefined();
    cache.request("b", 0, ok(2)); // viewing b again keeps it
    cache.request("d", 0, ok(4)); // now c goes
    expect(cache.get("c", 0)).toBeUndefined();
    expect(cache.get("b", 0)).toBeDefined();
    expect(cache.get("d", 0)).toBeDefined();
  });

  it("forgets a failed download so it can be tried again", async () => {
    const cache = new MaskCache(() => {});
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, offline: false, message: "nope" })
      .mockResolvedValueOnce({ ok: true, value: new Uint8Array([1]) });
    cache.request("a", 0, fetcher);
    await vi.waitFor(() => expect(cache.get("a", 0)?.state).toBe("error"));
    cache.forget("a", 0);
    cache.request("a", 0, fetcher);
    await vi.waitFor(() => expect(cache.get("a", 0)?.state).toBe("ok"));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
