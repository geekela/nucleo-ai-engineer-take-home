import type { Result } from "./api";

export type MaskEntry = { state: "loading" } | { state: "ok"; mask: Uint8Array } | { state: "error"; message: string };

/**
 * Downloaded model masks, keyed by (upload, position in its structure list), so one upload's mask is
 * never drawn for another. Each mask is fetched once. A case-001 mask is about 26 MB, so only the
 * uploads viewed most recently keep theirs; going back to an older one downloads it again.
 */
export class MaskCache {
  private entries = new Map<string, MaskEntry>();
  private recent: string[] = []; // run ids, most recently viewed last

  constructor(
    private readonly onChange: () => void,
    private readonly maxRuns = 3,
  ) {}

  get(runId: string, index: number): MaskEntry | undefined {
    return this.entries.get(key(runId, index));
  }

  request(runId: string, index: number, fetcher: () => Promise<Result<Uint8Array>>): void {
    this.touch(runId);
    const k = key(runId, index);
    if (this.entries.has(k)) return;
    this.entries.set(k, { state: "loading" });
    this.onChange();
    fetcher().then((r) => {
      // The upload may have been cleared while its mask was downloading; don't bring it back.
      if (!this.entries.has(k)) return;
      this.entries.set(k, r.ok ? { state: "ok", mask: r.value } : { state: "error", message: r.message });
      this.onChange();
    });
  }

  /** Drop one mask (e.g. a failed download) so the next request fetches it again. */
  forget(runId: string, index: number): void {
    if (this.entries.delete(key(runId, index))) this.onChange();
  }

  private touch(runId: string): void {
    this.recent = [...this.recent.filter((r) => r !== runId), runId];
    if (this.recent.length <= this.maxRuns) return;
    const keep = new Set(this.recent.slice(-this.maxRuns));
    this.recent = [...keep];
    this.dropAllBut(keep);
  }

  /** Forget every mask that doesn't belong to one of these uploads. */
  retain(runIds: Set<string>): void {
    this.recent = this.recent.filter((r) => runIds.has(r));
    this.dropAllBut(runIds);
  }

  private dropAllBut(runIds: Set<string>): void {
    let changed = false;
    for (const k of [...this.entries.keys()]) {
      if (!runIds.has(k.slice(0, k.indexOf("\u0000")))) {
        this.entries.delete(k);
        changed = true;
      }
    }
    if (changed) this.onChange();
  }
}

const key = (runId: string, index: number) => `${runId}\u0000${index}`;
