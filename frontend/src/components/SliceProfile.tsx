import { useMemo, useRef } from "react";
import { rgbCss, type RGB } from "../palette";

export interface Lane {
  label: string;
  rgb: RGB;
  counts: Uint32Array; // marked voxels per slice
}

interface Props {
  slices: number;
  slice: number;
  lanes: Lane[];
  onSlice: (s: number) => void;
}

const LANE_H = 16;
const MIN_BAR = 1.5; // so a slice with a single voxel still shows

/**
 * How much lesion each slice holds, along the whole scan. The first lane grows up from the centre line
 * and the second grows down, on one shared scale, so model and reference can be compared slice by slice.
 * Click or drag to jump there; the slider below is the keyboard control.
 */
export default function SliceProfile({ slices, slice, lanes, onSlice }: Props) {
  const strip = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  const bars = useMemo(() => {
    let max = 0;
    for (const lane of lanes) for (const c of lane.counts) if (c > max) max = c;
    return lanes.map((lane) => {
      const out: { s: number; h: number }[] = [];
      lane.counts.forEach((c, s) => {
        if (c > 0) out.push({ s, h: Math.max(MIN_BAR, (c / max) * LANE_H) });
      });
      return out;
    });
  }, [lanes]);

  const jump = (clientX: number) => {
    const box = strip.current?.getBoundingClientRect();
    if (!box || box.width <= 0 || !Number.isFinite(clientX)) return;
    const s = Math.floor(((clientX - box.left) / box.width) * slices);
    onSlice(Math.max(0, Math.min(slices - 1, s)));
  };

  const height = lanes.length > 1 ? 2 * LANE_H : LANE_H;
  return (
    <div className="profile">
      <div className={`profile-labels lanes-${lanes.length}`} aria-hidden="true">
        {lanes.map((l) => (
          <span key={l.label} style={{ color: rgbCss(l.rgb) }}>
            {l.label}
          </span>
        ))}
      </div>
      <div
        ref={strip}
        className="profile-strip"
        data-testid="slice-profile"
        aria-hidden="true"
        onPointerDown={(e) => {
          dragging.current = true;
          e.currentTarget.setPointerCapture?.(e.pointerId);
          jump(e.clientX);
        }}
        onPointerMove={(e) => dragging.current && jump(e.clientX)}
        onPointerUp={() => (dragging.current = false)}
        onPointerCancel={() => (dragging.current = false)}
      >
        <svg viewBox={`0 0 ${slices} ${height}`} preserveAspectRatio="none" width="100%" height={height * 1.5}>
          {lanes.length > 1 && <rect className="baseline" x={0} y={LANE_H - 0.15} width={slices} height={0.3} />}
          {lanes.map((lane, i) => (
            <g key={lane.label} data-lane={lane.label} fill={rgbCss(lane.rgb)}>
              {bars[i].map(({ s, h }) => (
                // Lane 0 grows up to the centre line, lane 1 grows down from it.
                <rect key={s} x={s + 0.1} y={i === 0 ? LANE_H - h : LANE_H} width={0.8} height={h} />
              ))}
            </g>
          ))}
          <rect className="cursor" x={slice} y={0} width={1} height={height} />
        </svg>
      </div>
    </div>
  );
}
