"""Tests for core.psd.classifier — LayerClassifier.

All tests use mock PSD layer objects (no real PSD files required).
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import numpy as np

from psd_tools.constants import BlendMode

from core.psd.classifier import LayerClassifier


# ===================================================================
# Helpers
# ===================================================================

_NO_IMG = object()  # sentinel for "no composite_img provided"


def _mock_layer(
    kind: str = "pixel",
    is_group: bool = False,
    visible: bool = True,
    opacity: int = 255,
    name: str = "Layer",
    blend_mode=BlendMode.NORMAL,
    effects=None,
    children=None,
    bbox=(0, 0, 100, 100),
    composite_img=_NO_IMG,
):
    """Create a mock PSD layer for testing."""
    layer = MagicMock()
    layer.kind = kind
    layer.is_group.return_value = is_group
    layer.visible = visible
    layer.opacity = opacity
    layer.name = name
    layer.blend_mode = blend_mode
    layer.bbox = bbox
    layer.left = bbox[0]
    layer.top = bbox[1]
    layer.width = bbox[2] - bbox[0]
    layer.height = bbox[3] - bbox[1]

    if effects is not None:
        layer.effects = effects
    else:
        layer.effects = []

    if children is not None:
        layer.__iter__ = lambda self, _c=children: iter(_c)
    else:
        layer.__iter__ = lambda self: iter([])

    if composite_img is _NO_IMG:
        # Default: 10x10 white opaque image
        from PIL import Image
        img = Image.new("RGBA", (10, 10), (255, 255, 255, 255))
        layer.composite.return_value = img
    else:
        layer.composite.return_value = composite_img

    return layer


def _make_classifier(canvas_w: int = 375, canvas_h: int = 812):
    return LayerClassifier(canvas_w, canvas_h)


# ===================================================================
# is_text_layer
# ===================================================================

class TestIsTextLayer:
    def test_type_layer(self):
        lc = _make_classifier()
        layer = _mock_layer(kind="type")
        assert lc.is_text_layer(layer) is True

    def test_pixel_layer(self):
        lc = _make_classifier()
        layer = _mock_layer(kind="pixel")
        assert lc.is_text_layer(layer) is False

    def test_shape_layer(self):
        lc = _make_classifier()
        layer = _mock_layer(kind="shape")
        assert lc.is_text_layer(layer) is False


# ===================================================================
# is_group_layer
# ===================================================================

class TestIsGroupLayer:
    def test_group(self):
        lc = _make_classifier()
        layer = _mock_layer(is_group=True)
        assert lc.is_group_layer(layer) is True

    def test_not_group(self):
        lc = _make_classifier()
        layer = _mock_layer(is_group=False)
        assert lc.is_group_layer(layer) is False


# ===================================================================
# is_pixel_layer
# ===================================================================

class TestIsPixelLayer:
    def test_pixel(self):
        lc = _make_classifier()
        layer = _mock_layer(kind="pixel", is_group=False)
        assert lc.is_pixel_layer(layer) is True

    def test_text_not_pixel(self):
        lc = _make_classifier()
        layer = _mock_layer(kind="type", is_group=False)
        assert lc.is_pixel_layer(layer) is False

    def test_group_not_pixel(self):
        lc = _make_classifier()
        layer = _mock_layer(is_group=True)
        assert lc.is_pixel_layer(layer) is False


# ===================================================================
# has_expanding_effects
# ===================================================================

class TestHasExpandingEffects:
    def test_no_effects(self):
        lc = _make_classifier()
        layer = _mock_layer()
        layer.effects = []
        assert lc.has_expanding_effects(layer) is False

    def test_no_effects_attr(self):
        lc = _make_classifier()
        layer = MagicMock(spec=[])
        assert lc.has_expanding_effects(layer) is False

    def test_drop_shadow(self):
        lc = _make_classifier()
        effect = MagicMock()
        effect.enabled = True
        effect.__str__ = MagicMock(return_value="DropShadow")
        layer = _mock_layer(effects=[effect])
        assert lc.has_expanding_effects(layer) is True

    def test_outer_glow(self):
        lc = _make_classifier()
        effect = MagicMock()
        effect.enabled = True
        effect.__str__ = MagicMock(return_value="OuterGlow")
        layer = _mock_layer(effects=[effect])
        assert lc.has_expanding_effects(layer) is True

    def test_stroke_outf(self):
        lc = _make_classifier()
        effect = MagicMock()
        effect.enabled = True
        effect.__str__ = MagicMock(return_value="Stroke")
        desc = {b'Styl': MagicMock(enum=b'OutF')}
        effect.descriptor = desc
        layer = _mock_layer(effects=[effect])
        assert lc.has_expanding_effects(layer) is True

    def test_stroke_ctrf(self):
        lc = _make_classifier()
        effect = MagicMock()
        effect.enabled = True
        effect.__str__ = MagicMock(return_value="Stroke")
        desc = {b'Styl': MagicMock(enum=b'CtrF')}
        effect.descriptor = desc
        layer = _mock_layer(effects=[effect])
        assert lc.has_expanding_effects(layer) is True

    def test_stroke_insf_no_expand(self):
        lc = _make_classifier()
        effect = MagicMock()
        effect.enabled = True
        effect.__str__ = MagicMock(return_value="Stroke")
        desc = {b'Styl': MagicMock(enum=b'InsF')}
        effect.descriptor = desc
        layer = _mock_layer(effects=[effect])
        assert lc.has_expanding_effects(layer) is False

    def test_disabled_effect(self):
        lc = _make_classifier()
        effect = MagicMock()
        effect.enabled = False
        effect.__str__ = MagicMock(return_value="DropShadow")
        layer = _mock_layer(effects=[effect])
        assert lc.has_expanding_effects(layer) is False


# ===================================================================
# is_button_group
# ===================================================================

class TestIsButtonGroup:
    def test_not_group(self):
        lc = _make_classifier()
        layer = _mock_layer(is_group=False, name="按钮")
        assert lc.is_button_group(layer) is False

    def test_no_button_name(self):
        lc = _make_classifier()
        child_text = _mock_layer(kind="type", visible=True)
        child_img = _mock_layer(kind="pixel", is_group=False, visible=True)
        layer = _mock_layer(is_group=True, name="card", children=[child_text, child_img])
        assert lc.is_button_group(layer) is False

    def test_valid_button_chinese(self):
        lc = _make_classifier()
        child_text = _mock_layer(kind="type", visible=True)
        child_img = _mock_layer(kind="pixel", is_group=False, visible=True)
        layer = _mock_layer(is_group=True, name="立即按钮", children=[child_text, child_img])
        assert lc.is_button_group(layer) is True

    def test_valid_button_english(self):
        lc = _make_classifier()
        child_text = _mock_layer(kind="type", visible=True)
        child_img1 = _mock_layer(kind="pixel", is_group=False, visible=True)
        child_img2 = _mock_layer(kind="pixel", is_group=False, visible=True)
        layer = _mock_layer(is_group=True, name="Buy Button", children=[child_text, child_img1, child_img2])
        assert lc.is_button_group(layer) is True

    def test_multiple_text_not_button(self):
        lc = _make_classifier()
        child_text1 = _mock_layer(kind="type", visible=True)
        child_text2 = _mock_layer(kind="type", visible=True)
        child_img = _mock_layer(kind="pixel", is_group=False, visible=True)
        layer = _mock_layer(is_group=True, name="按钮组", children=[child_text1, child_text2, child_img])
        assert lc.is_button_group(layer) is False

    def test_hidden_children_skipped(self):
        lc = _make_classifier()
        child_text = _mock_layer(kind="type", visible=True)
        child_img = _mock_layer(kind="pixel", is_group=False, visible=True)
        child_hidden = _mock_layer(kind="type", visible=False)
        layer = _mock_layer(is_group=True, name="按钮", children=[child_text, child_img, child_hidden])
        assert lc.is_button_group(layer) is True

    def test_no_image_not_button(self):
        lc = _make_classifier()
        child_text = _mock_layer(kind="type", visible=True)
        layer = _mock_layer(is_group=True, name="Button", children=[child_text])
        assert lc.is_button_group(layer) is False


# ===================================================================
# is_pure_image_group
# ===================================================================

class TestIsPureImageGroup:
    def test_not_group(self):
        lc = _make_classifier()
        layer = _mock_layer(is_group=False)
        assert lc.is_pure_image_group(layer) is False

    def test_pure_images(self):
        lc = _make_classifier()
        child1 = _mock_layer(kind="pixel", is_group=False, visible=True)
        child2 = _mock_layer(kind="pixel", is_group=False, visible=True)
        layer = _mock_layer(is_group=True, children=[child1, child2])
        assert lc.is_pure_image_group(layer) is True

    def test_has_text_not_pure(self):
        lc = _make_classifier()
        child_img = _mock_layer(kind="pixel", is_group=False, visible=True)
        child_text = _mock_layer(kind="type", visible=True)
        layer = _mock_layer(is_group=True, children=[child_img, child_text])
        assert lc.is_pure_image_group(layer) is False

    def test_no_visible_children(self):
        lc = _make_classifier()
        child = _mock_layer(kind="pixel", visible=False)
        layer = _mock_layer(is_group=True, children=[child])
        assert lc.is_pure_image_group(layer) is False

    def test_recursive_subgroup(self):
        lc = _make_classifier()
        inner_child = _mock_layer(kind="pixel", is_group=False, visible=True)
        inner_group = _mock_layer(is_group=True, visible=True, children=[inner_child])
        inner_group.kind = "group"
        layer = _mock_layer(is_group=True, children=[inner_group])
        assert lc.is_pure_image_group(layer, recursive=True) is True

    def test_recursive_subgroup_has_text(self):
        lc = _make_classifier()
        inner_child = _mock_layer(kind="type", is_group=False, visible=True)
        inner_group = _mock_layer(is_group=True, visible=True, children=[inner_child])
        inner_group.kind = "group"
        layer = _mock_layer(is_group=True, children=[inner_group])
        assert lc.is_pure_image_group(layer, recursive=True) is False


# ===================================================================
# can_merge_group
# ===================================================================

class TestCanMergeGroup:
    def test_not_group(self):
        lc = _make_classifier()
        layer = _mock_layer(is_group=False)
        assert lc.can_merge_group(layer) is False

    def test_oversized_bbox(self):
        lc = _make_classifier(canvas_w=100, canvas_h=100)
        child = _mock_layer(kind="pixel", is_group=False, visible=True)
        layer = _mock_layer(
            is_group=True,
            children=[child],
            bbox=(0, 0, 250, 50),  # width=250 > 100*2
        )
        assert lc.can_merge_group(layer) is False

    def test_valid_pure_image_group(self):
        lc = _make_classifier()
        child = _mock_layer(kind="pixel", is_group=False, visible=True)
        layer = _mock_layer(is_group=True, children=[child], bbox=(0, 0, 100, 100))
        assert lc.can_merge_group(layer) is True

    def test_expanding_effect_blocks_merge(self):
        lc = _make_classifier()
        effect = MagicMock()
        effect.enabled = True
        effect.__str__ = MagicMock(return_value="DropShadow")
        child = _mock_layer(kind="pixel", is_group=False, visible=True, effects=[effect])
        layer = _mock_layer(is_group=True, children=[child], bbox=(0, 0, 100, 100))
        assert lc.can_merge_group(layer) is False

    def test_button_group_can_merge(self):
        lc = _make_classifier()
        child_text = _mock_layer(kind="type", visible=True)
        child_img = _mock_layer(kind="pixel", is_group=False, visible=True)
        layer = _mock_layer(is_group=True, name="按钮", children=[child_text, child_img], bbox=(0, 0, 100, 50))
        assert lc.can_merge_group(layer) is True


# ===================================================================
# should_skip_layer
# ===================================================================

class TestShouldSkipLayer:
    def test_invisible(self):
        lc = _make_classifier()
        layer = _mock_layer(visible=False)
        assert lc.should_skip_layer(layer) is True

    def test_zero_opacity(self):
        lc = _make_classifier()
        layer = _mock_layer(visible=True, opacity=0)
        assert lc.should_skip_layer(layer) is True

    def test_visible_opaque(self):
        lc = _make_classifier()
        layer = _mock_layer(visible=True, opacity=255)
        assert lc.should_skip_layer(layer) is False

    def test_group_not_skipped(self):
        lc = _make_classifier()
        layer = _mock_layer(is_group=True, visible=True, opacity=255)
        assert lc.should_skip_layer(layer) is False

    def test_text_not_skipped(self):
        lc = _make_classifier()
        layer = _mock_layer(kind="type", visible=True, opacity=255)
        assert lc.should_skip_layer(layer) is False

    def test_transparent_image(self):
        """A pixel layer that is fully transparent in alpha → skip."""
        from PIL import Image
        lc = _make_classifier()
        img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        layer = _mock_layer(kind="pixel", visible=True, opacity=255, composite_img=img)
        assert lc.should_skip_layer(layer) is True

    def test_opaque_image(self):
        from PIL import Image
        lc = _make_classifier()
        img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
        layer = _mock_layer(kind="pixel", visible=True, opacity=255, composite_img=img)
        assert lc.should_skip_layer(layer) is False

    def test_composite_none_skipped(self):
        lc = _make_classifier()
        layer = _mock_layer(kind="pixel", visible=True, opacity=255, composite_img=None)
        assert lc.should_skip_layer(layer) is True

    def test_composite_raises_skipped(self):
        lc = _make_classifier()
        layer = _mock_layer(kind="pixel", visible=True, opacity=255)
        layer.composite.side_effect = RuntimeError("decode error")
        assert lc.should_skip_layer(layer) is True


# ===================================================================
# get_blend_mode_css
# ===================================================================

class TestGetBlendModeCss:
    def test_normal_returns_none(self):
        lc = _make_classifier()
        layer = _mock_layer(blend_mode=BlendMode.NORMAL)
        assert lc.get_blend_mode_css(layer) is None

    def test_multiply(self):
        lc = _make_classifier()
        layer = _mock_layer(blend_mode=BlendMode.MULTIPLY)
        assert lc.get_blend_mode_css(layer) == "multiply"

    def test_screen(self):
        lc = _make_classifier()
        layer = _mock_layer(blend_mode=BlendMode.SCREEN)
        assert lc.get_blend_mode_css(layer) == "screen"

    def test_overlay(self):
        lc = _make_classifier()
        layer = _mock_layer(blend_mode=BlendMode.OVERLAY)
        assert lc.get_blend_mode_css(layer) == "overlay"

    def test_difference(self):
        lc = _make_classifier()
        layer = _mock_layer(blend_mode=BlendMode.DIFFERENCE)
        assert lc.get_blend_mode_css(layer) == "difference"

    def test_color_dodge(self):
        lc = _make_classifier()
        layer = _mock_layer(blend_mode=BlendMode.COLOR_DODGE)
        assert lc.get_blend_mode_css(layer) == "color-dodge"

    def test_luminosity(self):
        lc = _make_classifier()
        layer = _mock_layer(blend_mode=BlendMode.LUMINOSITY)
        assert lc.get_blend_mode_css(layer) == "luminosity"

    def test_pass_through_is_normal(self):
        lc = _make_classifier()
        layer = _mock_layer(blend_mode=BlendMode.PASS_THROUGH)
        assert lc.get_blend_mode_css(layer) is None

    def test_no_blend_mode_attr(self):
        lc = _make_classifier()
        layer = MagicMock(spec=[])
        assert lc.get_blend_mode_css(layer) is None

    def test_soft_light(self):
        lc = _make_classifier()
        layer = _mock_layer(blend_mode=BlendMode.SOFT_LIGHT)
        assert lc.get_blend_mode_css(layer) == "soft-light"

    def test_hard_light(self):
        lc = _make_classifier()
        layer = _mock_layer(blend_mode=BlendMode.HARD_LIGHT)
        assert lc.get_blend_mode_css(layer) == "hard-light"

    def test_darken(self):
        lc = _make_classifier()
        layer = _mock_layer(blend_mode=BlendMode.DARKEN)
        assert lc.get_blend_mode_css(layer) == "darken"

    def test_lighten(self):
        lc = _make_classifier()
        layer = _mock_layer(blend_mode=BlendMode.LIGHTEN)
        assert lc.get_blend_mode_css(layer) == "lighten"

    def test_hue(self):
        lc = _make_classifier()
        layer = _mock_layer(blend_mode=BlendMode.HUE)
        assert lc.get_blend_mode_css(layer) == "hue"

    def test_saturation(self):
        lc = _make_classifier()
        layer = _mock_layer(blend_mode=BlendMode.SATURATION)
        assert lc.get_blend_mode_css(layer) == "saturation"

    def test_color(self):
        lc = _make_classifier()
        layer = _mock_layer(blend_mode=BlendMode.COLOR)
        assert lc.get_blend_mode_css(layer) == "color"
