import numpy as np
import pytest

from app.masks import Mask, MaskError, dice, load_nifti_mask, same_grid
from tests.conftest import nifti_bytes

EYE = np.eye(4)


def mask(data, affine=EYE):
    return Mask(data=np.asarray(data, dtype=np.uint8), affine=np.asarray(affine, dtype=float), non_binary=False)


def blank(shape=(4, 3, 2)):
    return np.zeros(shape, dtype=np.uint8)


def test_known_overlap_dice_half():
    a, b = blank(), blank()
    a[0, 0, :] = 1
    a[1, 0, :] = 1  # 4 voxels
    b[1, 0, :] = 1
    b[2, 0, :] = 1  # 4 voxels, 2 shared
    r = dice(mask(a), mask(b))
    assert r.value == pytest.approx(0.5)
    assert (r.voxels_model, r.voxels_reference) == (4, 4)


def test_identical_dice_one():
    a = blank()
    a[0, 0, 0] = 1
    assert dice(mask(a), mask(a.copy())).value == 1.0


def test_both_empty():
    r = dice(mask(blank()), mask(blank()))
    assert r.value is None and r.message == "Both masks are empty."


def test_shape_mismatch_no_dice():
    r = dice(mask(blank((4, 3, 2))), mask(blank((4, 3, 3))))
    assert r.value is None and "different sizes" in r.message


def test_affine_offset_mismatch_no_dice():
    shifted = EYE.copy()
    shifted[0, 3] = 0.01
    ok, reason = same_grid(mask(blank()), mask(blank(), shifted))
    assert not ok and reason == "The masks are positioned differently in space."


def test_tiny_affine_difference_is_same_grid():
    nudged = EYE.copy()
    nudged[0, 3] = 1e-5
    a = blank()
    a[0, 0, 0] = 1
    assert dice(mask(a), mask(a.copy(), nudged)).value == 1.0


def test_volume_ml():
    a = np.ones((10, 10, 10), dtype=np.uint8)
    r = dice(mask(a), mask(a.copy()))
    assert r.ml_model == pytest.approx(1.0) and r.ml_reference == pytest.approx(1.0)


@pytest.mark.parametrize("gz", [True, False])
def test_loads_gzip_and_plain(gz):
    a = blank()
    a[1, 2, 1] = 1
    aff = np.diag([-0.7, -0.7, 1.0, 1.0])
    m = load_nifti_mask(nifti_bytes(a, aff, gz=gz))
    assert m.data.shape == (4, 3, 2) and m.data.dtype == np.uint8 and m.data[1, 2, 1] == 1
    assert np.allclose(m.affine, aff) and m.non_binary is False


def test_non_binary_values_flagged_and_binarised():
    a = blank()
    a[0, 0, 0] = 2
    m = load_nifti_mask(nifti_bytes(a, EYE))
    assert m.non_binary is True and m.data[0, 0, 0] == 1


def test_scl_slope_ignored_stored_values_binarised():
    a = blank()
    a[0, 0, 0] = 1
    m = load_nifti_mask(nifti_bytes(a, EYE, slope=0.5))
    assert m.data.sum() == 1 and m.data[0, 0, 0] == 1


def test_garbage_bytes_raise_mask_error():
    with pytest.raises(MaskError, match="couldn't be read"):
        load_nifti_mask(b"definitely not nifti")


def test_4d_raises_mask_error():
    with pytest.raises(MaskError):
        load_nifti_mask(nifti_bytes(np.zeros((2, 2, 2, 2), dtype=np.uint8), EYE))


def test_gzip_bomb_is_refused(monkeypatch):
    import gzip

    import app.masks as masks_module

    monkeypatch.setattr(masks_module, "MAX_UNCOMPRESSED_BYTES", 10_000)
    bomb = gzip.compress(b"\0" * 1_000_000)
    with pytest.raises(MaskError, match="too large"):
        load_nifti_mask(bomb)


def test_dice_reports_the_overlap_and_the_checked_grid():
    a, b = blank(), blank()
    a[0, 0, :] = 1
    a[1, 0, :] = 1
    b[1, 0, :] = 1
    b[2, 0, :] = 1
    r = dice(mask(a), mask(b))
    assert r.voxels_overlap == 2
    assert r.shape == (4, 3, 2)


def test_dice_on_different_grids_reports_no_overlap_or_shape():
    r = dice(mask(blank((4, 3, 2))), mask(blank((4, 3, 3))))
    assert r.voxels_overlap is None and r.shape is None


def test_lesion_counts_match_lesions_between_model_and_reference():
    from app.masks import lesion_counts

    ref, model = blank((10, 10, 4)), blank((10, 10, 4))
    ref[1:3, 1:3, 0:2] = 1  # reference lesion A
    ref[7:9, 7:9, 2:4] = 1  # reference lesion B (missed by the model)
    model[2:4, 2:4, 1:3] = 1  # overlaps A
    model[0, 9, 0] = 1  # a finding with no reference lesion
    r = lesion_counts(mask(model), mask(ref))
    assert (r.reference, r.detected, r.model, r.model_only) == (2, 1, 2, 1)


def test_lesion_counts_join_voxels_that_touch_only_at_a_corner():
    from app.masks import lesion_counts

    ref = blank((4, 4, 4))
    ref[0, 0, 0] = 1
    ref[1, 1, 1] = 1  # diagonal neighbour: the same lesion in 3D
    r = lesion_counts(mask(blank((4, 4, 4))), mask(ref))
    assert (r.reference, r.detected, r.model, r.model_only) == (1, 0, 0, 0)


def test_lesion_counts_need_the_same_grid():
    from app.masks import lesion_counts

    assert lesion_counts(mask(blank((4, 3, 2))), mask(blank((4, 3, 3)))) is None
