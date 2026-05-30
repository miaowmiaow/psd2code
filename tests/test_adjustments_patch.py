#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for core.render.adjustments_patch (BlackAndWhite adjustment layer support).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import numpy as np
import pytest

from core.render.adjustments_patch import apply_blackandwhite, register_adjustment_patches
from psd_tools.constants import ColorMode


class FakeBlackAndWhiteLayer:
    """Fake BlackAndWhite layer for testing."""
    def __init__(self, red=40, yellow=60, green=40, cyan=60, blue=20, magenta=80,
                 use_tint=False, tint_color=None):
        self.red = red
        self.yellow = yellow
        self.green = green
        self.cyan = cyan
        self.blue = blue
        self.magenta = magenta
        self.use_tint = use_tint
        self.tint_color = tint_color or {}


class TestApplyBlackAndWhite:
    """Tests for apply_blackandwhite function."""

    def test_pure_red_pixel(self):
        """Pure red pixel should use reds weight."""
        # shape (1, 1, 4) - RGBA
        img = np.array([[[1.0, 0.0, 0.0, 1.0]]], dtype=np.float32)
        layer = FakeBlackAndWhiteLayer(red=100)
        result = apply_blackandwhite(img, ColorMode.RGB, layer)
        # reds=100 → weight=1.0, luminance=max(1,0,0)=1.0 → gray=1.0
        assert result.shape == (1, 1, 4)
        # RGB channels should be equal (grayscale)
        assert abs(result[0, 0, 0] - result[0, 0, 1]) < 0.01
        assert abs(result[0, 0, 1] - result[0, 0, 2]) < 0.01
        # Alpha preserved
        assert result[0, 0, 3] == pytest.approx(1.0)
        # Gray value should be ~1.0 (red=100 → weight=1.0)
        assert result[0, 0, 0] == pytest.approx(1.0, abs=0.05)

    def test_pure_red_pixel_half_weight(self):
        """Red pixel with reds=50 should produce gray=0.5."""
        img = np.array([[[1.0, 0.0, 0.0, 1.0]]], dtype=np.float32)
        layer = FakeBlackAndWhiteLayer(red=50)
        result = apply_blackandwhite(img, ColorMode.RGB, layer)
        assert result[0, 0, 0] == pytest.approx(0.5, abs=0.05)

    def test_pure_yellow_pixel(self):
        """Yellow pixel (R=1, G=1, B=0) → hue=60° → between reds and yellows."""
        img = np.array([[[1.0, 1.0, 0.0, 1.0]]], dtype=np.float32)
        layer = FakeBlackAndWhiteLayer(red=40, yellow=100)
        result = apply_blackandwhite(img, ColorMode.RGB, layer)
        # hue = 1.0 (60°/60°), idx=1 → weights[1]=yellows=1.0
        # luminance = max(1,1,0) = 1.0
        # gray = 1.0 * 1.0 = 1.0
        assert result[0, 0, 0] == pytest.approx(1.0, abs=0.05)

    def test_grayscale_pixel_preserved(self):
        """Pure gray pixel (R=G=B) should preserve luminance."""
        gray_val = 0.6
        img = np.array([[[gray_val, gray_val, gray_val, 1.0]]], dtype=np.float32)
        layer = FakeBlackAndWhiteLayer()
        result = apply_blackandwhite(img, ColorMode.RGB, layer)
        # delta=0 → preserve luminance
        assert result[0, 0, 0] == pytest.approx(gray_val, abs=0.01)

    def test_output_is_grayscale(self):
        """Output R, G, B channels should be equal for any input."""
        np.random.seed(42)
        img = np.random.rand(10, 10, 4).astype(np.float32)
        img[:, :, 3] = 1.0  # full alpha
        layer = FakeBlackAndWhiteLayer()
        result = apply_blackandwhite(img, ColorMode.RGB, layer)
        # All RGB channels should be equal
        assert np.allclose(result[:, :, 0], result[:, :, 1], atol=0.001)
        assert np.allclose(result[:, :, 1], result[:, :, 2], atol=0.001)

    def test_alpha_preserved(self):
        """Alpha channel should not be modified."""
        img = np.array([[[0.8, 0.2, 0.5, 0.7]]], dtype=np.float32)
        layer = FakeBlackAndWhiteLayer()
        result = apply_blackandwhite(img, ColorMode.RGB, layer)
        assert result[0, 0, 3] == pytest.approx(0.7)

    def test_tint_mode(self):
        """With tint enabled, output should be tinted (R != G != B)."""
        img = np.array([[[1.0, 0.0, 0.0, 1.0]]], dtype=np.float32)
        layer = FakeBlackAndWhiteLayer(
            red=100, use_tint=True,
            tint_color={b'Rd  ': 255.0, b'Grn ': 200.0, b'Bl  ': 100.0}
        )
        result = apply_blackandwhite(img, ColorMode.RGB, layer)
        # With tint, channels should be different
        assert result[0, 0, 0] > result[0, 0, 1] > result[0, 0, 2]

    def test_clamp_output(self):
        """Output should be clamped to [0, 1]."""
        # High weight (300%) on a bright pixel
        img = np.array([[[1.0, 0.0, 0.0, 1.0]]], dtype=np.float32)
        layer = FakeBlackAndWhiteLayer(red=300)
        result = apply_blackandwhite(img, ColorMode.RGB, layer)
        assert result[0, 0, 0] <= 1.0

    def test_negative_weight(self):
        """Negative weight should be clamped to 0."""
        img = np.array([[[1.0, 0.0, 0.0, 1.0]]], dtype=np.float32)
        layer = FakeBlackAndWhiteLayer(red=-200)
        result = apply_blackandwhite(img, ColorMode.RGB, layer)
        assert result[0, 0, 0] >= 0.0


class TestPatchedDrawStrokeEffect:
    """Tests for _patched_draw_stroke_effect (fixes full-coverage mask bug)."""

    @staticmethod
    def _make_stroke_desc(style_enum, size=2.0):
        """Create a mock descriptor for stroke effect."""
        from psd_tools.terminology import Enum, Key

        class FakeEnum:
            def __init__(self, val):
                self.enum = val

        class FakeColorDesc:
            classID = b'RGBC'
            def __contains__(self, key):
                return key in (Key.Red, Key.Green, Key.Blue)
            def __getitem__(self, key):
                mapping = {Key.Red: 95.0, Key.Green: 134.0, Key.Blue: 24.0}
                return mapping[key]

        class FakeDesc(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        desc = FakeDesc({
            Key.PaintType: FakeEnum(Enum.SolidColor),
            Key.Color: FakeColorDesc(),
            Key.Style: FakeEnum(style_enum),
            Key.SizeKey: size,
        })
        return desc

    def test_uniform_shape_returns_empty_mask(self):
        """When shape is all 1s (fully filled), stroke mask should be all 0s."""
        from core.render.adjustments_patch import _patched_draw_stroke_effect
        from psd_tools.terminology import Enum

        class FakePSD:
            color_mode = ColorMode.RGB

        desc = self._make_stroke_desc(Enum.InsetFrame)
        viewport = (0, 0, 100, 50)
        shape = np.ones((50, 100, 1), dtype=np.float32)

        color, mask = _patched_draw_stroke_effect(viewport, shape, desc, FakePSD())
        # Mask should be all zeros since shape is uniform (no edges)
        assert mask.shape == (50, 100, 1)
        assert np.all(mask == 0.0), f"Expected all-zero mask, got max={mask.max()}"

    def test_empty_shape_returns_empty_mask(self):
        """When shape is all 0s, stroke mask should also be all 0s."""
        from core.render.adjustments_patch import _patched_draw_stroke_effect
        from psd_tools.terminology import Enum

        class FakePSD:
            color_mode = ColorMode.RGB

        desc = self._make_stroke_desc(Enum.InsetFrame)
        viewport = (0, 0, 100, 50)
        shape = np.zeros((50, 100, 1), dtype=np.float32)

        color, mask = _patched_draw_stroke_effect(viewport, shape, desc, FakePSD())
        assert np.all(mask == 0.0)

    def test_shape_with_edges_produces_nonzero_mask(self):
        """When shape has actual edges, mask should be non-zero near edges."""
        from core.render.adjustments_patch import _patched_draw_stroke_effect
        from psd_tools.terminology import Enum

        class FakePSD:
            color_mode = ColorMode.RGB

        desc = self._make_stroke_desc(Enum.InsetFrame, size=4.0)
        viewport = (0, 0, 100, 50)
        # Create shape with clear edges (rectangle inside)
        shape = np.zeros((50, 100, 1), dtype=np.float32)
        shape[10:40, 20:80, 0] = 1.0

        color, mask = _patched_draw_stroke_effect(viewport, shape, desc, FakePSD())
        # Mask should have non-zero values near the edges
        assert mask.max() > 0.0, "Expected non-zero mask for shape with edges"


class TestRegistration:
    """Tests for register_adjustment_patches."""

    def test_blackandwhite_registered(self):
        """blackandwhite should be registered in ADJUSTMENT_FUNC."""
        register_adjustment_patches()
        from psd_tools.composite.composite import ADJUSTMENT_FUNC
        assert 'blackandwhite' in ADJUSTMENT_FUNC
        assert ADJUSTMENT_FUNC['blackandwhite'] is apply_blackandwhite

    def test_stroke_effect_patched(self):
        """draw_stroke_effect should be patched in Compositor globals."""
        from core.render.adjustments_patch import (
            _patched_draw_stroke_effect,
            register_stroke_effect_patch,
        )
        register_stroke_effect_patch()
        from psd_tools.composite.composite import Compositor
        patched_fn = Compositor._apply_stroke_effect.__globals__['draw_stroke_effect']
        assert patched_fn is _patched_draw_stroke_effect
