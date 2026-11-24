"""Load the CT from a DICOM zip, and map NIfTI masks onto its voxel grid.

DICOM geometry (LPS):
  IOP[0:3] = direction of increasing column index, IOP[3:6] = increasing row index.
  PixelSpacing = [spacing between rows, spacing between columns].
  So column step = PixelSpacing[1] * IOP[0:3], row step = PixelSpacing[0] * IOP[3:6].
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import numpy as np
import pydicom
from pydicom.errors import InvalidDicomError

from app.masks import Mask

GAP_ATOL_MM = 1e-3
ALIGN_ATOL = 1e-3
LPS_TO_RAS = np.diag([-1.0, -1.0, 1.0, 1.0])


class CtLoadError(Exception):
    """The CT can't be loaded. str() is a plain sentence."""


class GridMismatch(Exception):
    def __init__(self) -> None:
        super().__init__("This mask doesn't line up with the CT grid, so it can't be overlaid.")


@dataclass
class CtVolume:
    voxels: np.ndarray  # int16, (slices, rows, cols), HU
    index_to_lps: np.ndarray  # 4x4: (col, row, slice, 1) -> LPS mm
    spacing_mm: tuple[float, float, float]  # (slice, row, col)


def _skip(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return name.endswith("/") or any(p == "__MACOSX" or p.startswith(".") for p in parts) or parts[-1].upper() == "DICOMDIR"


def _read_slices(path: Path) -> list[pydicom.Dataset]:
    if not path.is_file():
        raise CtLoadError(f"CT not found. Put {path.name} in data/ and restart the app.")
    try:
        with zipfile.ZipFile(path) as zf:
            members = [i for i in zf.infolist() if not i.is_dir() and not _skip(i.filename)]
            return [pydicom.dcmread(io.BytesIO(zf.read(i))) for i in members]
    except zipfile.BadZipFile:
        raise CtLoadError("The CT file isn't a valid zip archive.") from None
    except (InvalidDicomError, EOFError, ValueError):
        raise CtLoadError("The CT zip contains files that aren't readable DICOM images.") from None


def load_dicom_zip(path: Path) -> CtVolume:
    slices = _read_slices(path)
    if len(slices) < 2:
        raise CtLoadError("The CT zip needs at least two DICOM slices.")
    irregular = CtLoadError("The CT slices don't form a regular 3D volume.")
    try:
        iop = np.array(slices[0].ImageOrientationPatient, dtype=float)
        spacing = np.array(slices[0].PixelSpacing, dtype=float)
        rows, cols = int(slices[0].Rows), int(slices[0].Columns)
        for ds in slices:
            if (
                not np.allclose(ds.ImageOrientationPatient, iop)
                or not np.allclose(ds.PixelSpacing, spacing)
                or (int(ds.Rows), int(ds.Columns)) != (rows, cols)
            ):
                raise irregular
        col_dir, row_dir = iop[:3], iop[3:]
        normal = np.cross(col_dir, row_dir)
        slices.sort(key=lambda ds: float(np.dot(np.array(ds.ImagePositionPatient, dtype=float), normal)))
        positions = np.array([ds.ImagePositionPatient for ds in slices], dtype=float)
    except AttributeError:
        raise CtLoadError("Some CT slices are missing position or orientation information.") from None

    gaps = np.diff(positions @ normal)
    if gaps.min() <= 0 or not np.allclose(gaps, gaps[0], atol=GAP_ATOL_MM):
        raise irregular
    slice_step = (positions[-1] - positions[0]) / (len(slices) - 1)

    m = np.eye(4)
    m[:3, 0] = spacing[1] * col_dir
    m[:3, 1] = spacing[0] * row_dir
    m[:3, 2] = slice_step
    m[:3, 3] = positions[0]

    try:
        hu = [ds.pixel_array * float(getattr(ds, "RescaleSlope", 1)) + float(getattr(ds, "RescaleIntercept", 0)) for ds in slices]
    except Exception:  # pixel decoding failures come in many types
        raise CtLoadError("The CT pixel data couldn't be decoded.") from None
    voxels = np.clip(np.stack(hu), -32768, 32767).astype(np.int16)
    return CtVolume(voxels=voxels, index_to_lps=m, spacing_mm=(float(gaps[0]), float(spacing[0]), float(spacing[1])))


_LPS_LETTERS = (("R", "L"), ("A", "P"), ("I", "S"))  # (negative, positive) end of each LPS axis


def _towards(direction: np.ndarray) -> str:
    axis = int(np.argmax(np.abs(direction)))
    return _LPS_LETTERS[axis][1 if direction[axis] > 0 else 0]


def edge_labels(index_to_lps: np.ndarray) -> dict[str, str]:
    """Which patient side each edge of the displayed slice faces (columns run left to right, rows top to bottom)."""
    col_dir, row_dir = index_to_lps[:3, 0], index_to_lps[:3, 1]
    return {"left": _towards(-col_dir), "right": _towards(col_dir), "top": _towards(-row_dir), "bottom": _towards(row_dir)}


def align_to_ct(mask: Mask, ct: CtVolume) -> np.ndarray:
    """Return the mask resampled (by exact index mapping only) to CT (slices, rows, cols) order.

    T = inv(mask affine) @ LPS->RAS @ CT index->LPS maps CT (col, row, slice) to mask (i, j, k).
    It must be an axis permutation with ±1 steps and integer offsets, and must map the CT grid
    one-to-one onto the mask grid; otherwise the mask isn't on this CT's grid and we refuse.
    """
    try:
        t = np.linalg.inv(mask.affine) @ LPS_TO_RAS @ ct.index_to_lps
    except np.linalg.LinAlgError:
        raise GridMismatch() from None
    rot, offset = t[:3, :3], t[:3, 3]
    rounded = np.round(rot)
    if (
        not np.allclose(rot, rounded, atol=ALIGN_ATOL)
        or not np.allclose(offset, np.round(offset), atol=ALIGN_ATOL)
        or not (np.abs(rounded).sum(axis=0) == 1).all()
        or not (np.abs(rounded).sum(axis=1) == 1).all()
    ):
        raise GridMismatch()

    ct_sizes = (ct.voxels.shape[2], ct.voxels.shape[1], ct.voxels.shape[0])  # (cols, rows, slices)
    mask_axis_for_ct = [0, 0, 0]
    index_for_ct: list[np.ndarray] = [np.empty(0, dtype=int)] * 3
    for m_axis in range(3):
        p = int(np.argmax(np.abs(rounded[m_axis])))
        sign, off = int(rounded[m_axis, p]), int(round(offset[m_axis]))
        idx = sign * np.arange(ct_sizes[p]) + off
        if ct_sizes[p] != mask.data.shape[m_axis] or idx.min() < 0 or idx.max() >= mask.data.shape[m_axis]:
            raise GridMismatch()
        mask_axis_for_ct[p] = m_axis
        index_for_ct[p] = idx

    in_ct_order = mask.data.transpose(mask_axis_for_ct)  # axes now (col, row, slice)
    picked = in_ct_order[np.ix_(*index_for_ct)]
    return np.ascontiguousarray(picked.transpose(2, 1, 0), dtype=np.uint8)
