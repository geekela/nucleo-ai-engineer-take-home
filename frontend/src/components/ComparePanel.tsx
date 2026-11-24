import type { DiceView, RunView } from "../api";
import { clockTime, formatMl } from "../format";
import { BOTH_RGB, modelColour, REFERENCE_RGB, rgbCss, type RGB } from "../palette";

function AgreementBar({ dice, modelRgb }: { dice: DiceView; modelRgb: RGB }) {
  const { voxels_model: m, voxels_reference: r, voxels_overlap: both, ml_model, ml_reference } = dice;
  if (m == null || r == null || both == null || m + r - both === 0) return null;
  const modelOnly = m - both;
  const refOnly = r - both;
  const total = modelOnly + both + refOnly;
  const mlPerModelVoxel = m > 0 && ml_model != null ? ml_model / m : 0;
  const mlPerRefVoxel = r > 0 && ml_reference != null ? ml_reference / r : 0;
  const parts = [
    { key: "model", label: "Model only", n: modelOnly, ml: modelOnly * mlPerModelVoxel, rgb: modelRgb },
    { key: "both", label: "Both", n: both, ml: both * (mlPerModelVoxel || mlPerRefVoxel), rgb: BOTH_RGB },
    { key: "ref", label: "Reference only", n: refOnly, ml: refOnly * mlPerRefVoxel, rgb: REFERENCE_RGB },
  ];
  return (
    <div className="agreement">
      <div className="bar" role="img" aria-label={`${both} voxels in both, ${modelOnly} in the model only, ${refOnly} in the reference only`}>
        {parts.map((p) => (p.n > 0 ? <span key={p.key} style={{ flexGrow: p.n / total, background: rgbCss(p.rgb) }} /> : null))}
      </div>
      <ul className="bar-legend">
        {parts.map((p) => (
          <li key={p.key}>
            <span className={`swatch ${p.key === "ref" ? "outline" : "fill"}`} style={{ color: rgbCss(p.rgb) }} />
            <span className="legend-label">{p.label}</span>
            <span className="legend-value">{formatMl(p.ml)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function ComparePanel({ run, modelRgb = modelColour(0) }: { run: RunView | null; modelRgb?: RGB }) {
  const dice = run?.dice ?? null;
  return (
    <section className="compare" aria-labelledby="compare-title">
      <h2 id="compare-title">Model vs reference</h2>
      {run && (
        <p className="source">
          From {run.source}, uploaded {clockTime(run.created_at)}
        </p>
      )}
      {!run ? (
        <p className="empty">No model result yet. Upload case-001 to get the model's masks and the Dice score.</p>
      ) : run.warning ? (
        <p className="run-message warning">{run.warning}</p>
      ) : !dice ? (
        <p className="empty">This upload has no Dice score.</p>
      ) : dice.value == null ? (
        <p className="run-message warning">{dice.message}</p>
      ) : (
        <>
          <div className="score">
            <span className="dsc">{dice.value.toFixed(3)}</span>
            <span className="dsc-label">Dice similarity coefficient between the model's lesion mask and the reference</span>
          </div>
          <AgreementBar dice={dice} modelRgb={modelRgb} />
          <dl className="facts">
            {dice.ml_model != null && (
              <div>
                <dt>Model volume</dt>
                <dd>{formatMl(dice.ml_model)}</dd>
              </div>
            )}
            {dice.ml_reference != null && (
              <div>
                <dt>Reference volume</dt>
                <dd>{formatMl(dice.ml_reference)}</dd>
              </div>
            )}
            {dice.lesions && (
              <>
                <div>
                  <dt>Reference lesions the model found</dt>
                  <dd>
                    {dice.lesions.detected} of {dice.lesions.reference}
                  </dd>
                </div>
                <div>
                  <dt>Model findings with no reference lesion</dt>
                  <dd>{dice.lesions.model_only}</dd>
                </div>
              </>
            )}
          </dl>
          {dice.shape && (
            <p className="grid-check">
              Grid check passed: {dice.shape.join(" × ")} voxels, same affine.
            </p>
          )}
        </>
      )}
      <p className="disclaimer">Model masks are automatic segmentations to inspect, not a diagnosis.</p>
    </section>
  );
}
