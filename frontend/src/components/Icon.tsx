import type { RunStatus } from "../api";

/** Small status glyphs for the upload list. Decorative: the status is always written out too. */
export default function StatusIcon({ status }: { status: RunStatus }) {
  if (status === "done") {
    return (
      <svg className="glyph done" viewBox="0 0 16 16" aria-hidden="true">
        <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  if (status === "failed") {
    return (
      <svg className="glyph failed" viewBox="0 0 16 16" aria-hidden="true">
        <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
    );
  }
  if (status === "cancelled") {
    return (
      <svg className="glyph cancelled" viewBox="0 0 16 16" aria-hidden="true">
        <path d="M4 8h8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
    );
  }
  return <span className={`glyph spinner ${status}`} aria-hidden="true" />;
}
