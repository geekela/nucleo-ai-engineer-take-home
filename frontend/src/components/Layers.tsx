import type { ViewMode } from "../overlays";
import { rgbCss, type RGB } from "../palette";

export interface Layer {
  key: string;
  label: string;
  rgb: RGB;
  style: "fill" | "outline";
  available: boolean;
  checked: boolean;
  note: string | null;
  onRetry?: () => void; // offered when the mask failed to download
}

interface Props {
  layers: Layer[];
  onToggle: (key: string) => void;
  mode: ViewMode;
  onMode: (mode: ViewMode) => void;
  canCompare: boolean;
  opacity: number;
  onOpacity: (opacity: number) => void;
}

const MODES: { value: ViewMode; label: string; hint: string }[] = [
  { value: "overlay", label: "Overlay", hint: "Model filled, reference outlined" },
  { value: "agreement", label: "Agreement", hint: "Coloured by who marked each voxel" },
];

export default function Layers({ layers, onToggle, mode, onMode, canCompare, opacity, onOpacity }: Props) {
  return (
    <section className="layers" aria-labelledby="layers-title">
      <h2 id="layers-title">Layers</h2>
      <div className="segmented" role="radiogroup" aria-label="View">
        {MODES.map((m) => (
          <label key={m.value} title={m.hint} className={m.value === "agreement" && !canCompare ? "disabled" : undefined}>
            <input
              type="radio"
              name="view-mode"
              value={m.value}
              checked={mode === m.value}
              disabled={m.value === "agreement" && !canCompare}
              onChange={() => onMode(m.value)}
            />
            <span>{m.label}</span>
          </label>
        ))}
      </div>
      <ul className="layer-list">
        {layers.map((l) => (
          <li key={l.key} className={l.available ? undefined : "unavailable"}>
            <label className="layer">
              <input type="checkbox" role="switch" checked={l.checked && l.available} disabled={!l.available} onChange={() => onToggle(l.key)} />
              <span className="switch" aria-hidden="true" />
              <span className={`swatch ${l.style}`} style={{ color: rgbCss(l.rgb) }} aria-hidden="true" />
              <span className="layer-label">{l.label}</span>
            </label>
            {l.note && (
              <p className="note">
                {l.note}
                {l.onRetry && (
                  <>
                    {" "}
                    <button type="button" className="text inline" onClick={l.onRetry}>
                      Try again
                    </button>
                  </>
                )}
              </p>
            )}
          </li>
        ))}
      </ul>
      <label className="opacity">
        <span>Fill opacity</span>
        <input type="range" min={10} max={100} step={5} value={Math.round(opacity * 100)} aria-label="Fill opacity" onChange={(e) => onOpacity(Number(e.target.value) / 100)} />
        <span className="value">{Math.round(opacity * 100)}%</span>
      </label>
    </section>
  );
}
