import { describe, expect, it } from "vitest";
import type { RunView } from "../api";
import { statusLine } from "../format";

const base: RunView = {
  run_id: "r1",
  source: "case-001-dicom.zip",
  created_at: 1000,
  status: "queued",
  status_since: 1000,
  message: null,
  detail: null,
  retry_in_s: null,
  retry_at: null,
  case_id: null,
  warning: null,
  cleared: false,
  dice: null,
  structures: [],
};

describe("statusLine", () => {
  it("counts down to the next attempt while the service is busy", () => {
    const run = { ...base, status: "waiting_to_retry" as const, retry_at: 1010 };
    expect(statusLine(run, 1002.2)).toBe("Service busy. Retrying in 8 s");
    expect(statusLine(run, 1011)).toBe("Service busy. Retrying now");
  });

  it("shows how much of the zip has been sent", () => {
    expect(statusLine({ ...base, status: "uploading", upload_fraction: 0.456 }, 1000)).toBe("Uploading, 46%");
    expect(statusLine({ ...base, status: "uploading", upload_fraction: null }, 1000)).toBe("Uploading");
  });

  it("shows how long the service has been segmenting", () => {
    const run = { ...base, status: "segmenting" as const, status_since: 1000 };
    expect(statusLine(run, 1000.4)).toBe("Segmenting");
    expect(statusLine(run, 1006.9)).toBe("Segmenting, 6 s");
  });

  it("says what a waiting upload is waiting for", () => {
    expect(statusLine(base, 1000)).toBe("Waiting for a free upload slot");
  });
});
