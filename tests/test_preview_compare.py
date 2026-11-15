"""The preview's image comparison helpers: CompareResult.to_dict, delta_e, the policy
arguments, empty images, the diff picture and strip() layout. No Strata needed.

tests/test_preview.py covers the Strata policy defaults and the PNG round trip; this file
pins the remaining branches of usd_asset_gate/preview/compare.py.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from usd_asset_gate.preview import compare as cmp  # noqa: E402


def _img(color, shape=(4, 5)):
    out = np.zeros((*shape, 3), dtype=np.uint8)
    out[:] = color
    return out


def _black_with_white_top_row():
    """A black 4x5 image and a copy whose top row (5 of 20 pixels) is white: ΔE 100 there."""
    a = _img((0, 0, 0))
    b = a.copy()
    b[0] = (255, 255, 255)
    return a, b


# --- CompareResult.to_dict ------------------------------------------------------------


def test_to_dict_rounds_only_the_float_statistics_to_six_places():
    result = cmp.CompareResult(
        pixels=10,
        pixels_over=3,
        fraction_over=1 / 3,
        max_delta_e=12.3456789,
        mean_delta_e=0.1234564,
        visible_change=True,
    )
    assert result.to_dict() == {
        "pixels": 10,
        "pixels_over": 3,
        "fraction_over": 0.333333,
        "max_delta_e": 12.345679,
        "mean_delta_e": 0.123456,
        "visible_change": True,
    }
    assert result.fraction_over == 1 / 3  # the dataclass itself keeps full precision


def test_to_dict_key_order_follows_the_fields():
    result, _ = cmp.compare(_img(0), _img(0))
    assert list(result.to_dict()) == [
        "pixels",
        "pixels_over",
        "fraction_over",
        "max_delta_e",
        "mean_delta_e",
        "visible_change",
    ]


# --- delta_e --------------------------------------------------------------------------


def test_delta_e_is_per_pixel_symmetric_and_exactly_zero_where_pixels_match():
    a, b = _black_with_white_top_row()
    d = cmp.delta_e(a, b)
    assert d.shape == (4, 5)
    assert (d[1:] == 0.0).all()
    assert d[0] == pytest.approx([100.0] * 5, abs=1e-3)
    assert np.array_equal(d, cmp.delta_e(b, a))


# --- compare: statistics and policy arguments -----------------------------------------


def test_mean_and_max_delta_e_over_a_partly_changed_image():
    result, _ = cmp.compare(*_black_with_white_top_row())
    assert result.pixels == 20 and result.pixels_over == 5
    assert result.fraction_over == 0.25
    assert result.max_delta_e == pytest.approx(100.0, abs=1e-3)
    assert result.mean_delta_e == pytest.approx(25.0, abs=1e-3)


def test_threshold_argument_moves_the_per_pixel_cut_off():
    a, b = _black_with_white_top_row()
    assert cmp.compare(a, b, threshold=99.5)[0].pixels_over == 5
    above, diff = cmp.compare(a, b, threshold=100.5)
    assert above.pixels_over == 0 and not above.visible_change
    assert (diff == 0).all()  # nothing over the threshold is painted red


def test_max_fraction_argument_is_a_strict_upper_bound():
    a, b = _black_with_white_top_row()  # 25 % of the pixels differ
    assert not cmp.compare(a, b, max_fraction=0.25)[0].visible_change
    assert cmp.compare(a, b, max_fraction=0.24)[0].visible_change


def test_empty_images_count_as_entirely_changed_without_statistics():
    empty = np.zeros((0, 5, 3), dtype=np.uint8)
    result, diff = cmp.compare(empty, empty.copy())
    assert result.pixels == 0 and result.pixels_over == 0
    assert result.fraction_over == 1.0 and result.visible_change
    assert result.max_delta_e == 0.0 and result.mean_delta_e == 0.0
    assert diff.shape == (0, 5, 3)


# --- compare: the diff picture --------------------------------------------------------


def test_diff_dims_the_before_image_to_grey():
    # Luma of (200, 0, 0) is 0.299 * 200 = 59.8; dimmed by 0.35 that is 20.93, truncated to 20.
    a = _img((200, 0, 0))
    _, diff = cmp.compare(a, a.copy())
    assert diff.dtype == np.uint8 and diff.shape == a.shape
    assert (diff == 20).all()


def test_diff_red_is_128_plus_four_delta_e_capped_at_255():
    a, b = _black_with_white_top_row()  # ΔE 100: 128 + 400 is over the cap
    _, diff = cmp.compare(a, b)
    assert (diff[0] == (255, 0, 0)).all()
    assert (diff[1:] == 0).all()  # the black "before" stays black when dimmed

    c = _img((128, 128, 128), (1, 2))
    d = c.copy()
    d[0, 0] = (140, 128, 128)
    de = float(cmp.delta_e(c, d)[0, 0])
    assert cmp.DEFAULT_THRESHOLD < de < (255 - 128) / 4  # in the uncapped range
    _, diff = cmp.compare(c, d)
    assert tuple(diff[0, 0]) == (int(128.0 + de * 4.0), 0, 0)


# --- strip ----------------------------------------------------------------------------


def test_strip_pads_shorter_images_and_gaps_with_the_background():
    short, tall = _img((255, 0, 0), (2, 2)), _img((0, 0, 255), (4, 3))
    s = cmp.strip([short, tall], gap=2, background=(0, 255, 0))
    assert s.shape == (4, 7, 3) and s.dtype == np.uint8
    assert (s[:2, :2] == (255, 0, 0)).all()
    assert (s[2:, :2] == (0, 255, 0)).all()  # below the shorter image
    assert (s[:, 2:4] == (0, 255, 0)).all()  # the gap
    assert (s[:, 4:] == (0, 0, 255)).all()


def test_strip_default_gap_is_8_and_a_single_image_has_none():
    a = _img((1, 2, 3), (3, 2))
    assert cmp.strip([a, a, a]).shape == (3, 2 * 3 + 8 * 2, 3)
    single = cmp.strip([a])
    assert single.shape == a.shape and np.array_equal(single, a)
