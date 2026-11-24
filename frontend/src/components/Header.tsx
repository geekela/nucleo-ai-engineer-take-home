import { useRef } from "react";
import type { CtMeta } from "../api";

interface Props {
  meta: CtMeta | null;
  canUploadBundled: boolean;
  onUploadBundled: () => void;
  onUploadFiles: (files: File[]) => void;
}

export default function Header({ meta, canUploadBundled, onUploadBundled, onUploadFiles }: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const scan = meta
    ? `Abdomen CT, ${meta.shape[0]} axial slices of ${meta.spacing_mm[1].toFixed(2)} × ${meta.spacing_mm[2].toFixed(2)} × ${meta.spacing_mm[0].toFixed(2)} mm`
    : "Abdomen CT";
  return (
    <header className="masthead">
      <div className="identity">
        <h1>case-001</h1>
        <p className="scan">{scan}</p>
      </div>
      <div className="actions">
        <button type="button" className="secondary" onClick={() => fileInput.current?.click()}>
          Upload files…
        </button>
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          aria-label="Choose zip files to upload"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            e.target.value = ""; // so the same file can be chosen again
            if (files.length) onUploadFiles(files);
          }}
        />
        <button type="button" className="primary" onClick={onUploadBundled} disabled={!canUploadBundled}>
          Upload case-001
        </button>
      </div>
    </header>
  );
}
