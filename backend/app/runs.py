"""Upload runs: one state object per upload, so concurrent results can never mix.

Every stage of an upload writes only into its own Run, found by run_id.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np

from app.api_client import NucleoClient
from app.ct import CtVolume, GridMismatch, align_to_ct
from app.errors import ApiError, technical_detail, user_message
from app.masks import DiceResult, LesionCounts, Mask, MaskError, dice, lesion_counts, load_nifti_mask

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 157_286_400  # 150 MiB, the service's limit
DICE_STRUCTURE = "lesions"
UNEXPECTED = "Something unexpected went wrong with this upload."
CLEARED = "Masks from this older upload were cleared to save memory."
CANCELLED = "Upload cancelled."

RunStatus = Literal["queued", "uploading", "segmenting", "waiting_to_retry", "downloading_masks", "done", "failed", "cancelled"]
FINISHED: frozenset[str] = frozenset({"done", "failed", "cancelled"})


@dataclass
class StructureResult:
    name: str
    overlay: np.ndarray | None  # uint8, CT (slices, rows, cols)
    message: str | None
    compared: bool = False  # this is the mask the Dice score compares with the reference


@dataclass
class Run:
    run_id: str
    source: str
    created_at: float
    status: RunStatus = "queued"
    message: str | None = None
    detail: str | None = None
    retry_in_s: float | None = None
    retry_at: float | None = None  # wall-clock time of the next attempt, for a countdown
    upload_fraction: float | None = None  # share of the zip sent in the current attempt
    case_id: str | None = None
    warning: str | None = None
    structures: list[StructureResult] = field(default_factory=list)
    dice: DiceResult | None = None
    lesions: LesionCounts | None = None
    dice_message: str | None = None
    cleared: bool = False
    status_since: float = 0.0  # when `status` last changed

    def __post_init__(self) -> None:
        self.status_since = self.created_at

    def __setattr__(self, name: str, value: Any) -> None:
        # Every status write goes through here, so status_since can't drift from status.
        if name == "status" and getattr(self, "status", value) != value:
            object.__setattr__(self, "status_since", time.time())
        object.__setattr__(self, name, value)

    def to_view(self) -> dict[str, Any]:
        dice_view = None
        if self.dice is not None:
            d = self.dice
            dice_view = {
                "value": d.value,
                "message": d.message,
                "voxels_model": d.voxels_model,
                "voxels_reference": d.voxels_reference,
                "ml_model": d.ml_model,
                "ml_reference": d.ml_reference,
                "voxels_overlap": d.voxels_overlap,
                "shape": list(d.shape) if d.shape else None,
                "lesions": asdict(self.lesions) if self.lesions else None,
            }
        elif self.dice_message:
            dice_view = {"value": None, "message": self.dice_message}
        return {
            "run_id": self.run_id,
            "source": self.source,
            "created_at": self.created_at,
            "status": self.status,
            "status_since": self.status_since,
            "message": self.message,
            "detail": self.detail,
            "retry_in_s": self.retry_in_s,
            "retry_at": self.retry_at,
            "upload_fraction": self.upload_fraction,
            "case_id": self.case_id,
            "warning": self.warning,
            "cleared": self.cleared,
            "dice": dice_view,
            "structures": [
                {"name": s.name, "overlay_ok": s.overlay is not None, "message": s.message, "compared": s.compared}
                for s in self.structures
            ],
        }


def _mark_cancelled(run: Run) -> None:
    # Drop anything half-downloaded so a cancelled upload never shows partial results.
    run.status, run.message = "cancelled", CANCELLED
    run.retry_in_s = run.retry_at = run.upload_fraction = None
    run.structures, run.dice, run.lesions, run.dice_message = [], None, None, None


class RunManager:
    def __init__(
        self, client: NucleoClient, ct: CtVolume | None, reference: Mask | None, expected_case_id: str, max_kept: int = 10
    ) -> None:
        self._client = client
        self._ct = ct
        self._reference = reference
        self._expected = expected_case_id
        self._max_kept = max_kept
        self._runs: dict[str, Run] = {}  # insertion order = start order
        self._tasks: dict[str, asyncio.Task[None]] = {}  # run_id -> task, while the run is in progress

    def start(self, body: bytes, source: str) -> Run:
        run = Run(run_id=uuid.uuid4().hex, source=source, created_at=time.time())
        self._runs[run.run_id] = run
        task = asyncio.create_task(self._process(run, body))
        self._tasks[run.run_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run.run_id, None))
        return run

    def cancel(self, run_id: str) -> Run | None:
        """Stop an upload that is still in progress. A finished run is left as it is."""
        run = self._runs.get(run_id)
        task = self._tasks.get(run_id)
        if run is not None and task is not None and run.status not in FINISHED:
            task.cancel()
            _mark_cancelled(run)  # also covers a task cancelled before it ever ran
        return run

    def start_failed(self, source: str, message: str) -> Run:
        """Record a run that failed before anything could be sent (e.g. the bundled zip is missing)."""
        run = Run(run_id=uuid.uuid4().hex, source=source, created_at=time.time(), status="failed", message=message)
        self._runs[run.run_id] = run
        return run

    def clear_failed(self) -> int:
        """Forget failed and cancelled uploads. Results and uploads in progress stay. Returns how many went."""
        gone = [rid for rid, r in self._runs.items() if r.status in ("failed", "cancelled")]
        for rid in gone:
            del self._runs[rid]
        return len(gone)

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def list(self) -> list[Run]:
        return list(reversed(self._runs.values()))

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    async def _process(self, run: Run, body: bytes) -> None:
        try:
            await self._upload_and_fetch(run, body)
        except asyncio.CancelledError:
            _mark_cancelled(run)
        except ApiError as e:
            run.status, run.message, run.detail = "failed", user_message(e), technical_detail(e)
        except Exception:
            log.exception("run %s failed unexpectedly", run.run_id)
            run.status, run.message = "failed", UNEXPECTED
        finally:
            self._clear_old_runs()

    async def _upload_and_fetch(self, run: Run, body: bytes) -> None:
        if not body:
            raise ApiError("empty")
        if len(body) > MAX_UPLOAD_BYTES:
            raise ApiError("too_large")

        def on_queue(_: int) -> None:
            run.status, run.retry_in_s, run.retry_at = "queued", None, None

        def on_attempt(_: int) -> None:
            run.status, run.retry_in_s, run.retry_at, run.upload_fraction = "uploading", None, None, 0.0

        def on_progress(sent: int, total: int) -> None:
            run.upload_fraction = sent / total if total else 1.0
            if sent >= total:
                run.status = "segmenting"  # every byte is out; the service is working on it

        def on_wait(seconds: float, _: int) -> None:
            run.status, run.retry_in_s, run.retry_at = "waiting_to_retry", round(seconds, 1), time.time() + seconds

        try:
            result = await self._client.upload(
                body, on_attempt=on_attempt, on_wait=on_wait, on_queue=on_queue, on_progress=on_progress
            )
        finally:
            run.retry_in_s = run.retry_at = run.upload_fraction = None
        run.case_id = result.case_id
        if result.case_id != self._expected:
            run.warning = (
                f"The service identified this upload as {result.case_id}, not {self._expected}, "
                "so its masks aren't shown on this CT."
            )
            run.status = "done"
            return

        run.status = "downloading_masks"
        dice_mask: Mask | None = None
        for s in result.structures:
            mask, structure = await self._fetch_structure(s.name, s.url)
            if s.name == DICE_STRUCTURE and mask is not None and dice_mask is None:
                dice_mask, structure.compared = mask, True
            run.structures.append(structure)

        if dice_mask is None:
            run.dice_message = "No lesion mask in this result."
        elif self._reference is None:
            run.dice_message = "Reference mask not loaded."
        else:
            run.dice = await asyncio.to_thread(dice, dice_mask, self._reference)
            run.lesions = await asyncio.to_thread(lesion_counts, dice_mask, self._reference)
        run.status = "done"

    async def _fetch_structure(self, name: str, url: str) -> tuple[Mask | None, StructureResult]:
        try:
            raw = await self._client.download(url)
            mask = await asyncio.to_thread(load_nifti_mask, raw)
        except ApiError as e:
            return None, StructureResult(name, None, user_message(e))
        except MaskError as e:
            return None, StructureResult(name, None, str(e))
        if self._ct is None:
            return mask, StructureResult(name, None, "CT not loaded, so nothing to overlay on.")
        try:
            overlay = await asyncio.to_thread(align_to_ct, mask, self._ct)
        except GridMismatch as e:
            return mask, StructureResult(name, None, str(e))
        note = "Mask had values other than 0 and 1; anything above 0 is shown." if mask.non_binary else None
        return mask, StructureResult(name, overlay, note)

    def _clear_old_runs(self) -> None:
        """Keep mask arrays for the newest `max_kept` runs that have any; failed runs don't take a slot."""
        holding = [r for r in self.list() if any(s.overlay is not None for s in r.structures)]
        for run in holding[self._max_kept :]:
            for s in run.structures:
                s.overlay = None
            run.cleared = True
            run.message = CLEARED
