"""Shared test helpers. No test here talks to the real API."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config import Settings

TEST_TOKEN = "tid_test.secret"


class FakeSleep:
    """Records requested sleeps and returns at once (yielding to the event loop)."""

    def __init__(self, clock: FakeClock | None = None) -> None:
        self.calls: list[float] = []
        self.clock = clock

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self.clock is not None:
            self.clock.advance(seconds)
        await asyncio.sleep(0)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def fake_sleep() -> FakeSleep:
    return FakeSleep()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(base_url="https://api.test", token=TEST_TOKEN, data_dir=tmp_path)


def nifti_bytes(data, affine, slope: float | None = None, gz: bool = True) -> bytes:
    """Serialise an array as NIfTI-1 (gzipped by default), optionally with scl_slope set."""
    import gzip

    import nibabel as nib
    import numpy as np

    img = nib.Nifti1Image(np.asarray(data), np.asarray(affine, dtype=float))
    if slope is not None:
        img.header.set_slope_inter(slope, 0.0)
    raw = img.to_bytes()
    return gzip.compress(raw) if gz else raw


def dicom_bytes(pixels, position, iop=(1, 0, 0, 0, 1, 0), spacing=(1.0, 1.0), series_uid="1.2.3", instance=1) -> bytes:
    """A minimal uncompressed CT slice (int16, slope 1 / intercept 0)."""
    import io

    import numpy as np
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

    pixels = np.asarray(pixels, dtype=np.int16)
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.SeriesInstanceUID = series_uid
    ds.Modality = "CT"
    ds.InstanceNumber = instance
    ds.ImagePositionPatient = [float(v) for v in position]
    ds.ImageOrientationPatient = [float(v) for v in iop]
    ds.PixelSpacing = [float(v) for v in spacing]
    ds.Rows, ds.Columns = pixels.shape
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 1
    ds.RescaleSlope = 1
    ds.RescaleIntercept = 0
    ds.PixelData = pixels.tobytes()
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    return buf.getvalue()


def dicom_zip(n_slices=3, rows=3, cols=4, z_positions=None, shuffle=True, extra: dict[str, bytes] | None = None) -> bytes:
    """Zip of n_slices slices; slice s has every pixel equal to s. Origin (-10, -20, 5 + z)."""
    import io
    import random
    import zipfile

    import numpy as np

    zs = list(z_positions) if z_positions is not None else [5.0 + s for s in range(n_slices)]
    files = [
        (f"case/{s:04d}.dcm", dicom_bytes(np.full((rows, cols), s), (-10, -20, z), instance=s + 1)) for s, z in enumerate(zs)
    ]
    if shuffle:
        random.Random(0).shuffle(files)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files:
            zf.writestr(name, data)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def small_ct():
    """CT of (slices=2, rows=3, cols=4), 1 mm voxels, identity orientation, first voxel at LPS (-10, -20, 5)."""
    import numpy as np

    from app.ct import CtVolume

    m = np.eye(4)
    m[:3, 3] = (-10, -20, 5)
    return CtVolume(voxels=np.zeros((2, 3, 4), dtype=np.int16), index_to_lps=m, spacing_mm=(1.0, 1.0, 1.0))
