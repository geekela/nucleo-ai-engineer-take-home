import type { Status } from "../api";

function host(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/** The bottom bar: which segmentation service the app talks to, and the data credit. */
export default function StatusBar({ status }: { status: Status | null }) {
  return (
    <footer className="statusbar">
      {status && (
        <p className="service">
          <span className="service-label">Segmentation service</span>
          <span className="service-host" title={status.base_url}>
            {host(status.base_url)}
          </span>
          <span className={status.token_configured ? "token ok" : "token missing"}>{status.token_configured ? "Token set" : "No token"}</span>
        </p>
      )}
      <p className="credit">
        Data: Wasserthal, J. (2026). Liver lesions CT training dataset. Zenodo.{" "}
        <a href="https://doi.org/10.5281/zenodo.20272572">doi:10.5281/zenodo.20272572</a>, CC BY 4.0.
      </p>
    </footer>
  );
}
