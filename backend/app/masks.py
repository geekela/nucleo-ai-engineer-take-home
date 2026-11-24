"""Binary NIfTI masks, the same-grid check, and the Dice coefficient.

Masks stay in their native (i, j, k) voxel order with their own affine.
Dice is computed on that native grid, never on a reordered copy.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import nibabel as nib
import numpy as np
from scipy import ndimage

AFFINE_ATOL = 1e-3
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024  # a 416x350x180 uint8 mask is ~26 MB; this stops gzip bombs


class MaskError(Exception):
    """A mask file that can't be used. str() is a plain sentence."""


@dataclass
class Mask:
    data: np.ndarray  # uint8, (i, j, k), values 0/1
    affine: np.ndarray  # 4x4 voxel -> RAS+ mm
    non_binary: bool  # stored values other than 0/1 were present


def _gunzip_capped(raw: bytes) -> bytes:
    d = zlib.decompressobj(wbits=31)  # gzip framing
    out = d.decompress(raw, MAX_UNCOMPRESSED_BYTES + 1)
    if len(out) > MAX_UNCOMPRESSED_BYTES:
        raise MaskError("The mask file is too large once unpacked.")
    return out


def load_nifti_mask(raw: bytes) -> Mask:
    try:
        if raw[:2] == b"\x1f\x8b":
            raw = _gunzip_capped(raw)
        img = nib.Nifti1Image.from_bytes(raw)
        stored = np.asanyarray(img.dataobj.get_unscaled() if hasattr(img.dataobj, "get_unscaled") else img.dataobj)
    except MaskError:
        raise
    except Exception:  # nibabel/gzip raise many types for bad bytes
        raise MaskError("The mask file couldn't be read.") from None
    if stored.ndim != 3:
        raise MaskError("The mask file isn't a 3D volume.")
    non_binary = bool(np.any((stored != 0) & (stored != 1)))
    return Mask(data=(stored > 0).astype(np.uint8), affine=np.asarray(img.affine, dtype=float), non_binary=non_binary)


def same_grid(a: Mask, b: Mask) -> tuple[bool, str | None]:
    if a.data.shape != b.data.shape:
        return False, f"The masks have different sizes ({a.data.shape} vs {b.data.shape})."
    if not np.allclose(a.affine, b.affine, atol=AFFINE_ATOL):
        return False, "The masks are positioned differently in space."
    return True, None


@dataclass
class DiceResult:
    value: float | None
    message: str | None
    voxels_model: int
    voxels_reference: int
    ml_model: float
    ml_reference: float
    voxels_overlap: int | None = None  # set only when the grids match
    shape: tuple[int, int, int] | None = None  # the shared (i, j, k) grid, once checked


def _ml(m: Mask, voxels: int) -> float:
    return voxels * abs(float(np.linalg.det(m.affine[:3, :3]))) / 1000.0


def dice(model: Mask, reference: Mask) -> DiceResult:
    """DSC = 2|A∩B| / (|A| + |B|), only when both masks share a grid."""
    a, b = int(model.data.sum()), int(reference.data.sum())
    base = dict(voxels_model=a, voxels_reference=b, ml_model=_ml(model, a), ml_reference=_ml(reference, b))
    ok, reason = same_grid(model, reference)
    if not ok:
        return DiceResult(value=None, message=reason, **base)
    overlap = int(np.count_nonzero(model.data & reference.data))
    shape = tuple(int(n) for n in model.data.shape)
    if a + b == 0:
        return DiceResult(value=None, message="Both masks are empty.", voxels_overlap=0, shape=shape, **base)
    return DiceResult(value=2.0 * overlap / (a + b), message=None, voxels_overlap=overlap, shape=shape, **base)


@dataclass
class LesionCounts:
    reference: int  # lesions in the reference
    detected: int  # reference lesions the model touches
    model: int  # lesions in the model mask
    model_only: int  # model lesions that touch no reference lesion


_FULL_3D = np.ones((3, 3, 3), dtype=bool)  # 26-connectivity: voxels touching at a corner join


def lesion_counts(model: Mask, reference: Mask) -> LesionCounts | None:
    """Count lesions as 3D connected components and match them by any overlap. Same grid only."""
    if not same_grid(model, reference)[0]:
        return None
    ref_labels, n_ref = ndimage.label(reference.data, structure=_FULL_3D)
    model_labels, n_model = ndimage.label(model.data, structure=_FULL_3D)
    detected = len(np.unique(ref_labels[(ref_labels > 0) & (model.data > 0)]))
    matched_model = len(np.unique(model_labels[(model_labels > 0) & (reference.data > 0)]))
    return LesionCounts(reference=n_ref, detected=detected, model=n_model, model_only=n_model - matched_model)
