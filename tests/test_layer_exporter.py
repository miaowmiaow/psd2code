"""Tests for core.extract.layer_exporter — static/utility methods.

Covers:
- LayerExporter._is_clipping: clipping attribute detection
- LayerExporter._group_clipping_layers: chain grouping logic
- LayerExporter._intersect_bbox: rectangle intersection
- LayerExporter._blend_light_layer: light blend mode math (screen/dodge/add/lighten/lighter)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from core.extract.layer_exporter import LayerExporter


# ═══════════════════════════════════════════════════════════════════════════════
# _is_clipping
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsClipping:
    def test_clipping_layer(self):
        """Layer with _record.clipping == 1 is clipping."""
        layer = MagicMock()
        layer._record = MagicMock()
        layer._record.clipping = 1
        assert LayerExporter._is_clipping(layer) is True

    def test_non_clipping_layer(self):
        """Layer with _record.clipping == 0 is NOT clipping."""
        layer = MagicMock()
        layer._record = MagicMock()
        layer._record.clipping = 0
        assert LayerExporter._is_clipping(layer) is False

    def test_no_record_attribute(self):
        """Layer without _record attribute is NOT clipping."""
        layer = MagicMock(spec=[])  # no attributes
        assert LayerExporter._is_clipping(layer) is False

    def test_record_without_clipping(self):
        """Layer with _record but no clipping field is NOT clipping."""
        layer = MagicMock()
        layer._record = MagicMock(spec=[])  # _record has no .clipping
        assert LayerExporter._is_clipping(layer) is False

    def test_clipping_value_2(self):
        """Only clipping == 1 counts; other truthy values don't match."""
        layer = MagicMock()
        layer._record = MagicMock()
        layer._record.clipping = 2
        assert LayerExporter._is_clipping(layer) is False


# ═══════════════════════════════════════════════════════════════════════════════
# _group_clipping_layers
# ═══════════════════════════════════════════════════════════════════════════════

def _make_layer(name: str, clipping: int = 0):
    """Create a mock layer with _record.clipping."""
    layer = MagicMock()
    layer.name = name
    layer._record = MagicMock()
    layer._record.clipping = clipping
    return layer


class TestGroupClippingLayers:
    def test_no_clipping(self):
        """All normal layers → output unchanged."""
        a = _make_layer("A", 0)
        b = _make_layer("B", 0)
        c = _make_layer("C", 0)
        result = LayerExporter._group_clipping_layers([a, b, c])
        assert result == [a, b, c]

    def test_single_clipping_group(self):
        """Base + 1 clipped → (base, [clipped])."""
        base = _make_layer("Base", 0)
        clip = _make_layer("Clip", 1)
        result = LayerExporter._group_clipping_layers([base, clip])
        assert len(result) == 1
        assert isinstance(result[0], tuple)
        assert result[0][0] is base
        assert result[0][1] == [clip]

    def test_multiple_clipped_to_one_base(self):
        """Base + N clipped → (base, [clip1, clip2, ...])."""
        base = _make_layer("Base", 0)
        c1 = _make_layer("C1", 1)
        c2 = _make_layer("C2", 1)
        c3 = _make_layer("C3", 1)
        result = LayerExporter._group_clipping_layers([base, c1, c2, c3])
        assert len(result) == 1
        assert result[0] == (base, [c1, c2, c3])

    def test_mixed_groups(self):
        """Multiple bases with different clipped layers."""
        a = _make_layer("A", 0)
        a_clip = _make_layer("A_clip", 1)
        b = _make_layer("B", 0)
        c = _make_layer("C", 0)
        c_clip1 = _make_layer("C_clip1", 1)
        c_clip2 = _make_layer("C_clip2", 1)

        result = LayerExporter._group_clipping_layers([a, a_clip, b, c, c_clip1, c_clip2])
        assert len(result) == 3
        assert result[0] == (a, [a_clip])
        assert result[1] is b
        assert result[2] == (c, [c_clip1, c_clip2])

    def test_orphan_clipping_layer(self):
        """Clipping layer at start (no base) → treated as normal layer."""
        orphan = _make_layer("Orphan", 1)
        normal = _make_layer("Normal", 0)
        result = LayerExporter._group_clipping_layers([orphan, normal])
        assert len(result) == 2
        assert result[0] is orphan
        assert result[1] is normal

    def test_empty_list(self):
        result = LayerExporter._group_clipping_layers([])
        assert result == []

    def test_consecutive_orphans(self):
        """Multiple consecutive clipping layers without base → all treated as normal."""
        c1 = _make_layer("C1", 1)
        c2 = _make_layer("C2", 1)
        c3 = _make_layer("C3", 1)
        result = LayerExporter._group_clipping_layers([c1, c2, c3])
        # First orphan c1 is standalone, then c2 is orphan, then c3 is orphan
        # Actually: c1 is clipping (orphan) → standalone;
        # c2 is clipping (orphan) → standalone; c3 is clipping (orphan) → standalone
        assert len(result) == 3

    def test_normal_after_clipping_group(self):
        """After a clipping group, normal layers continue."""
        base = _make_layer("Base", 0)
        clip = _make_layer("Clip", 1)
        after = _make_layer("After", 0)
        result = LayerExporter._group_clipping_layers([base, clip, after])
        assert len(result) == 2
        assert result[0] == (base, [clip])
        assert result[1] is after


# ═══════════════════════════════════════════════════════════════════════════════
# _intersect_bbox
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntersectBbox:
    def test_overlapping(self):
        a = (0, 0, 100, 100)
        b = (50, 50, 150, 150)
        result = LayerExporter._intersect_bbox(a, b)
        assert result == (50, 50, 100, 100)

    def test_contained(self):
        """b is fully inside a."""
        a = (0, 0, 200, 200)
        b = (50, 50, 100, 100)
        result = LayerExporter._intersect_bbox(a, b)
        assert result == (50, 50, 100, 100)

    def test_no_overlap_horizontal(self):
        a = (0, 0, 50, 100)
        b = (60, 0, 100, 100)
        result = LayerExporter._intersect_bbox(a, b)
        assert result is None

    def test_no_overlap_vertical(self):
        a = (0, 0, 100, 50)
        b = (0, 60, 100, 100)
        result = LayerExporter._intersect_bbox(a, b)
        assert result is None

    def test_touching_edge(self):
        """Touching at edge (right==left) → no intersection."""
        a = (0, 0, 50, 50)
        b = (50, 0, 100, 50)
        result = LayerExporter._intersect_bbox(a, b)
        assert result is None

    def test_touching_corner(self):
        a = (0, 0, 50, 50)
        b = (50, 50, 100, 100)
        result = LayerExporter._intersect_bbox(a, b)
        assert result is None

    def test_identical(self):
        a = (10, 20, 30, 40)
        result = LayerExporter._intersect_bbox(a, a)
        assert result == (10, 20, 30, 40)

    def test_single_pixel_overlap(self):
        a = (0, 0, 10, 10)
        b = (9, 9, 20, 20)
        result = LayerExporter._intersect_bbox(a, b)
        assert result == (9, 9, 10, 10)

    def test_negative_coords(self):
        a = (-10, -10, 10, 10)
        b = (-5, -5, 5, 5)
        result = LayerExporter._intersect_bbox(a, b)
        assert result == (-5, -5, 5, 5)


# ═══════════════════════════════════════════════════════════════════════════════
# _blend_light_layer
# ═══════════════════════════════════════════════════════════════════════════════

class TestBlendLightLayer:
    """Test light blend mode math with small arrays."""

    def _make_base(self, r=0.5, g=0.5, b=0.5, a=1.0):
        """Create a 2x2 RGBA float32 array."""
        arr = np.zeros((2, 2, 4), dtype=np.float32)
        arr[:, :, 0] = r
        arr[:, :, 1] = g
        arr[:, :, 2] = b
        arr[:, :, 3] = a
        return arr

    def _make_light(self, r=1.0, g=1.0, b=1.0, a=1.0):
        """Create a 2x2 RGBA float32 array."""
        arr = np.zeros((2, 2, 4), dtype=np.float32)
        arr[:, :, 0] = r
        arr[:, :, 1] = g
        arr[:, :, 2] = b
        arr[:, :, 3] = a
        return arr

    def test_screen_blend(self):
        """SCREEN: 1 - (1-bg)*(1-fg)"""
        base = self._make_base(0.5, 0.5, 0.5, 1.0)
        light = self._make_light(0.5, 0.5, 0.5, 1.0)
        result = LayerExporter._blend_light_layer(base, light, "BlendMode.SCREEN", 1.0)
        # screen(0.5, 0.5) = 1 - 0.5*0.5 = 0.75
        expected_rgb = 0.75
        np.testing.assert_allclose(result[:, :, 0], expected_rgb, atol=1e-5)
        np.testing.assert_allclose(result[:, :, 1], expected_rgb, atol=1e-5)
        np.testing.assert_allclose(result[:, :, 2], expected_rgb, atol=1e-5)
        # Output alpha = base alpha
        np.testing.assert_allclose(result[:, :, 3], 1.0, atol=1e-5)

    def test_linear_dodge_add(self):
        """LINEAR_DODGE (add): bg + fg, clamped."""
        base = self._make_base(0.6, 0.4, 0.3, 1.0)
        light = self._make_light(0.5, 0.7, 0.9, 1.0)
        result = LayerExporter._blend_light_layer(base, light, "BlendMode.LINEAR_DODGE", 1.0)
        # 0.6+0.5=1.0 (clamped), 0.4+0.7=1.0 (clamped), 0.3+0.9=1.0 (clamped)
        np.testing.assert_allclose(result[:, :, 0], 1.0, atol=1e-5)
        np.testing.assert_allclose(result[:, :, 1], 1.0, atol=1e-5)
        np.testing.assert_allclose(result[:, :, 2], 1.0, atol=1e-5)

    def test_color_dodge(self):
        """COLOR_DODGE: bg / (1 - fg)."""
        base = self._make_base(0.5, 0.5, 0.5, 1.0)
        light = self._make_light(0.5, 0.5, 0.5, 1.0)
        result = LayerExporter._blend_light_layer(base, light, "BlendMode.COLOR_DODGE", 1.0)
        # 0.5 / (1 - 0.5) = 0.5 / 0.5 = 1.0
        np.testing.assert_allclose(result[:, :, 0], 1.0, atol=1e-5)

    def test_lighten(self):
        """LIGHTEN: max(bg, fg)."""
        base = self._make_base(0.3, 0.7, 0.5, 1.0)
        light = self._make_light(0.6, 0.4, 0.5, 1.0)
        result = LayerExporter._blend_light_layer(base, light, "BlendMode.LIGHTEN", 1.0)
        np.testing.assert_allclose(result[:, :, 0], 0.6, atol=1e-5)  # max(0.3, 0.6)
        np.testing.assert_allclose(result[:, :, 1], 0.7, atol=1e-5)  # max(0.7, 0.4)
        np.testing.assert_allclose(result[:, :, 2], 0.5, atol=1e-5)  # max(0.5, 0.5)

    def test_lighter_color(self):
        """LIGHTER_COLOR: pixel-wise, pick the color with higher luminance."""
        # bg luminance: 0.299*0.2 + 0.587*0.2 + 0.114*0.2 = 0.2
        # fg luminance: 0.299*0.8 + 0.587*0.8 + 0.114*0.8 = 0.8
        base = self._make_base(0.2, 0.2, 0.2, 1.0)
        light = self._make_light(0.8, 0.8, 0.8, 1.0)
        result = LayerExporter._blend_light_layer(base, light, "BlendMode.LIGHTER_COLOR", 1.0)
        # fg is brighter → use fg
        np.testing.assert_allclose(result[:, :, 0], 0.8, atol=1e-5)

    def test_opacity_reduces_effect(self):
        """Half opacity should halve the blend effect."""
        base = self._make_base(0.0, 0.0, 0.0, 1.0)
        light = self._make_light(1.0, 1.0, 1.0, 1.0)
        # SCREEN(0, 1) = 1 - (1-0)*(1-1) = 1.0
        # With opacity=0.5: mix_factor = light_a * opacity * has_bg = 1.0*0.5*1.0 = 0.5
        # out_rgb = bg*(1-mix_factor) + blended*mix_factor = 0*0.5 + 1.0*0.5 = 0.5
        result = LayerExporter._blend_light_layer(base, light, "BlendMode.SCREEN", 0.5)
        np.testing.assert_allclose(result[:, :, 0], 0.5, atol=1e-5)

    def test_transparent_base_no_effect(self):
        """When base alpha is 0, light effect should NOT produce pixels."""
        base = self._make_base(0.0, 0.0, 0.0, 0.0)  # fully transparent
        light = self._make_light(1.0, 1.0, 1.0, 1.0)
        result = LayerExporter._blend_light_layer(base, light, "BlendMode.SCREEN", 1.0)
        # has_bg = (bg_a > 1e-6) = False → mix_factor = 0
        # out_rgb = bg_rgb * 1.0 + blended * 0.0 = 0
        # out_alpha = bg_a = 0
        np.testing.assert_allclose(result[:, :, 3], 0.0, atol=1e-5)
        np.testing.assert_allclose(result[:, :, 0], 0.0, atol=1e-5)

    def test_output_alpha_equals_base_alpha(self):
        """Output alpha should always equal base alpha (light doesn't expand coverage)."""
        base = self._make_base(0.5, 0.5, 0.5, 0.7)
        light = self._make_light(1.0, 1.0, 1.0, 1.0)
        result = LayerExporter._blend_light_layer(base, light, "BlendMode.SCREEN", 1.0)
        np.testing.assert_allclose(result[:, :, 3], 0.7, atol=1e-5)

    def test_black_light_screen_no_change(self):
        """Black fg under SCREEN is identity (1 - (1-bg)*(1-0) = bg)."""
        base = self._make_base(0.6, 0.4, 0.8, 1.0)
        light = self._make_light(0.0, 0.0, 0.0, 1.0)
        result = LayerExporter._blend_light_layer(base, light, "BlendMode.SCREEN", 1.0)
        # screen(bg, 0) = 1 - (1-bg)*1 = bg
        # mix_factor = 1.0
        # out = base*(1-1) + blended*1 = blended = bg (for screen with fg=0)
        np.testing.assert_allclose(result[:, :, 0], 0.6, atol=1e-5)
        np.testing.assert_allclose(result[:, :, 1], 0.4, atol=1e-5)
        np.testing.assert_allclose(result[:, :, 2], 0.8, atol=1e-5)

    def test_fallback_normal_blend(self):
        """Unknown blend mode falls back to normal (fg_rgb)."""
        base = self._make_base(0.5, 0.5, 0.5, 1.0)
        light = self._make_light(0.9, 0.1, 0.3, 1.0)
        result = LayerExporter._blend_light_layer(base, light, "BlendMode.UNKNOWN_MODE", 1.0)
        # blended = fg_rgb, mix=1.0*1.0=1.0 (has_bg=True)
        # out = base*(1-1) + blended*1 = blended
        np.testing.assert_allclose(result[:, :, 0], 0.9, atol=1e-5)
        np.testing.assert_allclose(result[:, :, 1], 0.1, atol=1e-5)
        np.testing.assert_allclose(result[:, :, 2], 0.3, atol=1e-5)

    def test_partial_light_alpha(self):
        """Light layer with partial alpha has reduced effect."""
        base = self._make_base(0.0, 0.0, 0.0, 1.0)
        light = self._make_light(1.0, 1.0, 1.0, 0.5)  # half-transparent light
        # SCREEN(0, 1) = 1.0
        # mix_factor = light_a * opacity * has_bg = 0.5 * 1.0 * 1.0 = 0.5
        # out = 0*(1-0.5) + 1.0*0.5 = 0.5
        result = LayerExporter._blend_light_layer(base, light, "BlendMode.SCREEN", 1.0)
        np.testing.assert_allclose(result[:, :, 0], 0.5, atol=1e-5)

    def test_values_clamped_to_01(self):
        """Output RGB and alpha should be clamped to [0,1]."""
        # COLOR_DODGE with small denominator can produce > 1
        base = self._make_base(0.9, 0.9, 0.9, 1.0)
        light = self._make_light(0.99, 0.99, 0.99, 1.0)
        result = LayerExporter._blend_light_layer(base, light, "BlendMode.COLOR_DODGE", 1.0)
        assert result[:, :, :3].max() <= 1.0
        assert result[:, :, :3].min() >= 0.0
