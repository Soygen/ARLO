"""Tests for physical/logical scale computation."""

import pytest

from arc_helper.screen.base import compute_scale


def test_identity():
    assert compute_scale(3840, 3840) == 1.0


def test_fractional_175_percent():
    assert compute_scale(3840, 2194) == pytest.approx(1.7502, abs=1e-3)


def test_zero_logical_width_is_safe():
    assert compute_scale(3840, 0) == 1.0


def test_negative_logical_width_is_safe():
    assert compute_scale(3840, -5) == 1.0
