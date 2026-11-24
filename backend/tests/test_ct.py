import numpy as np
import pytest

from app.ct import CtLoadError, GridMismatch, align_to_ct, load_dicom_zip
from app.masks import Mask
from tests.conftest import dicom_zip

# Mask affine that matches small_ct exactly: RAS x = 10 - i, y = 20 - j, z = 5 + k.
MATCHING = np.array([[-1, 0, 0, 10], [0, -1, 0, 20], [0, 0, 1, 5], [0, 0, 0, 1]], dtype=float)


def write(tmp_path, data):
    p = tmp_path / "ct.zip"
    p.write_bytes(data)
    return p


def mask_with_voxel(ijk, affine=MATCHING, shape=(4, 3, 2)):
    d = np.zeros(shape, dtype=np.uint8)
    d[ijk] = 1
    return Mask(data=d, affine=affine, non_binary=False)


def test_shuffled_slices_load_in_position_order(tmp_path):
    ct = load_dicom_zip(write(tmp_path, dicom_zip(n_slices=3)))
    assert ct.voxels.shape == (3, 3, 4) and ct.voxels.dtype == np.int16
    assert [int(ct.voxels[s, 0, 0]) for s in range(3)] == [0, 1, 2]
    assert ct.spacing_mm == (1.0, 1.0, 1.0)


def test_index_to_lps_origin_and_steps(tmp_path):
    ct = load_dicom_zip(write(tmp_path, dicom_zip(n_slices=3)))
    assert np.allclose(ct.index_to_lps @ [0, 0, 0, 1], [-10, -20, 5, 1])
    assert np.allclose(ct.index_to_lps @ [3, 2, 1, 1], [-7, -18, 6, 1])


def test_macosx_and_dotfiles_ignored(tmp_path):
    extra = {"__MACOSX/case/._0000.dcm": b"junk", "case/.DS_Store": b"junk", "DICOMDIR": b"junk"}
    ct = load_dicom_zip(write(tmp_path, dicom_zip(n_slices=2, extra=extra)))
    assert ct.voxels.shape[0] == 2


def test_uneven_spacing_rejected(tmp_path):
    with pytest.raises(CtLoadError, match="regular 3D volume"):
        load_dicom_zip(write(tmp_path, dicom_zip(n_slices=3, z_positions=[5.0, 6.0, 8.0])))


def test_missing_file_mentions_data_dir(tmp_path):
    with pytest.raises(CtLoadError, match="data/"):
        load_dicom_zip(tmp_path / "nope.zip")


def test_not_a_zip(tmp_path):
    with pytest.raises(CtLoadError):
        load_dicom_zip(write(tmp_path, b"hello"))


def test_aligned_voxel_lands_on_ct_index(small_ct):
    out = align_to_ct(mask_with_voxel((3, 1, 0)), small_ct)
    assert out.shape == (2, 3, 4) and out.dtype == np.uint8
    assert out[0, 1, 3] == 1 and out.sum() == 1


def test_k_flipped_affine_lands_on_other_slice(small_ct):
    flipped = MATCHING.copy()
    flipped[2] = [0, 0, -1, 6]
    out = align_to_ct(mask_with_voxel((3, 1, 0), flipped), small_ct)
    assert out[1, 1, 3] == 1 and out.sum() == 1


def test_rotated_affine_refused(small_ct):
    c, s = np.cos(np.pi / 6), np.sin(np.pi / 6)
    rot = MATCHING.copy()
    rot[:2, :2] = [[-c, s], [-s, -c]]
    with pytest.raises(GridMismatch, match="doesn't line up"):
        align_to_ct(mask_with_voxel((0, 0, 0), rot), small_ct)


def test_half_voxel_offset_refused(small_ct):
    off = MATCHING.copy()
    off[0, 3] += 0.5
    with pytest.raises(GridMismatch):
        align_to_ct(mask_with_voxel((0, 0, 0), off), small_ct)


def test_mask_not_matching_ct_extent_refused(small_ct):
    with pytest.raises(GridMismatch):
        align_to_ct(mask_with_voxel((0, 0, 0), shape=(4, 3, 3)), small_ct)


def test_edge_labels_for_a_standard_axial_series():
    from app.ct import edge_labels

    m = np.eye(4)  # columns run toward patient left (+x LPS), rows toward posterior (+y LPS)
    assert edge_labels(m) == {"left": "R", "right": "L", "top": "A", "bottom": "P"}


def test_edge_labels_follow_a_flipped_series():
    from app.ct import edge_labels

    m = np.diag([-0.7, -0.7, 1.0, 1.0])  # columns toward patient right, rows toward anterior
    assert edge_labels(m) == {"left": "L", "right": "R", "top": "P", "bottom": "A"}
