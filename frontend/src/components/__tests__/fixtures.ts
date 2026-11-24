import type { RunView } from "../../api";

export function run(over: Partial<RunView> = {}): RunView {
  return {
    run_id: "run-1",
    source: "case-001-dicom.zip",
    created_at: 1_000,
    status: "done",
    status_since: 1_000,
    message: null,
    detail: null,
    retry_in_s: null,
    retry_at: null,
    case_id: "case-001",
    warning: null,
    cleared: false,
    dice: null,
    structures: [],
    ...over,
  };
}
