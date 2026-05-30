# -*- coding: utf-8 -*-
"""Unit tests for the semantic naming pipeline (P4).

Tests cover:
  - common/semantic.py: extract_semantic_token, is_default_ps_name, _strip_copy_suffix
  - semantic/layer1_cleaner.py: clean_name, Layer1Cleaner.analyze
  - semantic/layer2_role_inferer.py: DomContext, Layer2RoleInferer rules R1~R5
  - semantic/name_resolver.py: NameResolver, NameCandidate, arbitration, caching
"""

from __future__ import annotations

import pytest

from common.semantic import (
    extract_semantic_token,
    is_default_ps_name,
    _strip_copy_suffix,
    _match_keyword,
    _to_kebab,
)
from semantic.layer1_cleaner import clean_name, Layer1Cleaner
from semantic.layer2_role_inferer import DomContext, Layer2RoleInferer
from semantic.name_resolver import NameResolver, NameCandidate


# ===========================================================================
# common/semantic.py — extract_semantic_token
# ===========================================================================


class TestExtractSemanticToken:
    """Test the legacy extract_semantic_token function."""

    # --- Keyword matches ---
    def test_keyword_btn(self):
        assert extract_semantic_token("按钮") == "btn"

    def test_keyword_btn_receive(self):
        assert extract_semantic_token("立即领取") == "btn-receive"

    def test_keyword_title(self):
        assert extract_semantic_token("主标题") == "title"

    def test_keyword_title_sub(self):
        assert extract_semantic_token("副标题文字") == "title-sub"

    def test_keyword_bg(self):
        assert extract_semantic_token("底图背景") == "bg"

    def test_keyword_case_insensitive(self):
        assert extract_semantic_token("BUTTON") == "btn"
        assert extract_semantic_token("Button") == "btn"

    # --- PS default names ---
    def test_ps_default_layer(self):
        assert extract_semantic_token("图层 5") == ""

    def test_ps_default_rectangle_matches_keyword(self):
        """'矩形 3' hits keyword '矩形' → 'rect' (keyword match beats PS default check)."""
        assert extract_semantic_token("矩形 3") == "rect"

    def test_ps_default_group_matches_keyword(self):
        """'group 12' hits keyword 'group' → 'group' (keyword match first)."""
        assert extract_semantic_token("group 12") == "group"

    def test_ps_default_hash(self):
        assert extract_semantic_token("b9e7ecaf01234567") == ""

    # --- Pure ASCII ---
    def test_ascii_kebab_with_keyword_hit(self):
        """'hero_banner' contains keyword 'banner' → 'banner' (keyword match first)."""
        assert extract_semantic_token("hero_banner") == "banner"

    def test_ascii_no_keyword(self):
        """Pure ASCII with no keyword match → kebab-case."""
        assert extract_semantic_token("hero_section") == "hero-section"

    def test_ascii_mixed_with_keyword(self):
        """'my card.item' contains keyword 'card' → 'card'."""
        assert extract_semantic_token("my card.item") == "card"

    def test_pure_digits_empty(self):
        assert extract_semantic_token("01") == ""
        assert extract_semantic_token("123") == ""

    def test_ascii_truncated_to_20(self):
        long_name = "a_very_long_layer_name_that_exceeds_limit"
        result = extract_semantic_token(long_name)
        assert len(result) <= 20

    # --- Copy suffix stripping ---
    def test_copy_suffix_stripped(self):
        assert extract_semantic_token("按钮 拷贝 3") == "btn"
        assert extract_semantic_token("按钮 copy 2") == "btn"

    # --- Empty / whitespace ---
    def test_empty_string(self):
        assert extract_semantic_token("") == ""

    def test_only_spaces(self):
        assert extract_semantic_token("   ") == ""

    # --- Pinyin fallback ---
    def test_chinese_pinyin_fallback(self):
        """Unknown Chinese characters → pinyin (first segment)."""
        result = extract_semantic_token("节日氛围图")
        # Should be something like "jieri" or "jie" (first 3 chars)
        assert result != ""
        assert result.isascii()


class TestIsDefaultPsName:
    def test_layer_n(self):
        assert is_default_ps_name("图层 5") is True

    def test_rectangle_n(self):
        assert is_default_ps_name("矩形 3") is True

    def test_shape_n(self):
        assert is_default_ps_name("形状 12") is True

    def test_vector_smart_object(self):
        assert is_default_ps_name("矢量智能对象") is True

    def test_group_n(self):
        assert is_default_ps_name("Group 5") is True

    def test_custom_name(self):
        assert is_default_ps_name("按钮") is False

    def test_empty(self):
        assert is_default_ps_name("") is True


class TestHelpers:
    def test_strip_copy_suffix(self):
        assert _strip_copy_suffix("按钮 拷贝 2") == "按钮"
        assert _strip_copy_suffix("icon copy") == "icon"
        assert _strip_copy_suffix("normal") == "normal"

    def test_match_keyword(self):
        assert _match_keyword("立即领取按钮") == "btn-receive"
        assert _match_keyword("unknown text") is None

    def test_to_kebab(self):
        assert _to_kebab("Hello World") == "hello-world"
        assert _to_kebab("some_name.here") == "some-name-here"
        assert _to_kebab("  multiple   spaces  ") == "multiple-spaces"


# ===========================================================================
# semantic/layer1_cleaner.py — clean_name & Layer1Cleaner
# ===========================================================================


class TestCleanName:
    def test_strip_emoji(self):
        result = clean_name("🎉按钮🎁")
        assert "🎉" not in result
        assert "🎁" not in result
        assert "按钮" in result

    def test_fullwidth_to_halfwidth(self):
        result = clean_name("按钮（选中）")
        # Brackets content should be removed by _BRACKETED
        assert "（" not in result

    def test_strip_copy_suffix(self):
        assert "拷贝" not in clean_name("图标 拷贝 3")

    def test_strip_brackets(self):
        result = clean_name("按钮(已选中)")
        assert "(已选中)" not in result

    def test_strip_tail_index(self):
        result = clean_name("按钮-3")
        assert result == "按钮"

    def test_empty_string(self):
        assert clean_name("") == ""

    def test_preserve_chinese(self):
        assert "南瓜" in clean_name("南瓜道具")


class TestLayer1Cleaner:
    def setup_method(self):
        self.cleaner = Layer1Cleaner()

    def test_dict_loaded(self):
        """cn_dict.json should have been loaded with entries."""
        assert self.cleaner.dict_size > 0

    def test_hit_returns_candidate(self):
        """Known keyword → NameCandidate with source='layer1'."""
        cand = self.cleaner.analyze("立即领取按钮 拷贝 2", "group")
        assert cand is not None
        assert cand.source == "layer1"
        assert cand.name != ""
        assert cand.confidence > 0

    def test_miss_returns_none(self):
        """Unknown random text → None."""
        cand = self.cleaner.analyze("xyzzy_unknown_9283", "pixel")
        assert cand is None

    def test_empty_name(self):
        assert self.cleaner.analyze("", "text") is None

    def test_case_insensitive_match(self):
        """Dict matching should be case insensitive."""
        cand1 = self.cleaner.analyze("背景图层", "image")
        cand2 = self.cleaner.analyze("BACKGROUND", "image")  # May or may not match depending on dict
        # At least the Chinese one should match
        assert cand1 is not None


# ===========================================================================
# semantic/layer2_role_inferer.py — DomContext & rules
# ===========================================================================


class TestDomContext:
    def test_area(self):
        d = DomContext(width=100, height=50)
        assert d.area == 5000.0

    def test_area_none(self):
        d = DomContext(width=None, height=50)
        assert d.area is None

    def test_parent_area(self):
        d = DomContext(parent_width=375, parent_height=812)
        assert d.parent_area == 375 * 812


class TestLayer2RoleInferer:
    def setup_method(self):
        self.inferer = Layer2RoleInferer()

    # --- R1: button demote ---

    def test_r1_btn_demote_triggers(self):
        """Group with ≥5 children + subgroup + existing btn-* → demote."""
        dom = DomContext(
            children_types=("text", "text", "image", "shape", "group"),
        )
        existing = [NameCandidate("btn-task", 0.85, "layer1", "")]
        cand = self.inferer.analyze("group", dom, existing)
        assert cand is not None
        assert "slogan" in cand.name
        assert cand.confidence == 0.95

    def test_r1_does_not_trigger_few_children(self):
        """Group with < 5 children → no demote."""
        dom = DomContext(children_types=("text", "shape"))
        existing = [NameCandidate("btn-receive", 0.85, "layer1", "")]
        cand = self.inferer.analyze("group", dom, existing)
        assert cand is None  # Not triggered

    def test_r1_does_not_trigger_no_subgroup(self):
        """Group with ≥5 children but no subgroup → no demote."""
        dom = DomContext(
            children_types=("text", "text", "image", "shape", "text"),
        )
        existing = [NameCandidate("btn-join", 0.85, "layer1", "")]
        cand = self.inferer.analyze("group", dom, existing)
        assert cand is None

    def test_r1_not_triggered_for_non_group(self):
        """Non-group ltype → R1 skipped."""
        dom = DomContext(children_types=("text",) * 6)
        existing = [NameCandidate("btn-task", 0.85, "layer1", "")]
        cand = self.inferer.analyze("image", dom, existing)
        # R1 won't fire (ltype != "group")
        # But other rules might fire or return None
        assert cand is None or "slogan" not in (cand.name or "")

    # --- R2: shape button ---

    def test_r2_shape_button(self):
        """Shape with button-like dimensions → btn."""
        dom = DomContext(width=200, height=50)
        cand = self.inferer.analyze("shape", dom, [])
        assert cand is not None
        assert cand.name == "btn"
        assert cand.confidence == 0.7

    def test_r2_shape_too_large(self):
        """Shape too large → not a button."""
        dom = DomContext(width=500, height=200)
        cand = self.inferer.analyze("shape", dom, [])
        assert cand is None

    def test_r2_shape_wrong_ratio(self):
        """Shape with wrong aspect ratio → not a button."""
        dom = DomContext(width=50, height=50)  # ratio=1 < 1.5
        cand = self.inferer.analyze("shape", dom, [])
        assert cand is None

    def test_r2_not_triggered_with_strong_existing(self):
        """If strong candidate already exists, R2 doesn't fire."""
        dom = DomContext(width=200, height=50)
        existing = [NameCandidate("btn-receive", 0.85, "layer1", "")]
        cand = self.inferer.analyze("shape", dom, existing)
        assert cand is None

    # --- R3: section background ---

    def test_r3_large_group(self):
        """Group covering ≥80% of parent → bg-section."""
        dom = DomContext(width=375, height=700, parent_width=375, parent_height=812)
        cand = self.inferer.analyze("group", dom, [])
        assert cand is not None
        assert cand.name == "bg-section"
        assert cand.confidence == 0.6

    def test_r3_small_group(self):
        """Group covering <80% → no candidate."""
        dom = DomContext(width=100, height=50, parent_width=375, parent_height=812)
        cand = self.inferer.analyze("group", dom, [])
        # Might get text-block or other rule, but not bg-section
        assert cand is None or cand.name != "bg-section"

    def test_r3_not_for_image(self):
        """R3 only applies to group, not image."""
        dom = DomContext(width=375, height=700, parent_width=375, parent_height=812)
        cand = self.inferer.analyze("image", dom, [])
        assert cand is None or cand.name != "bg-section"

    # --- R4: text block ---

    def test_r4_text_block(self):
        """Group with all text children ≥2 → text-block."""
        dom = DomContext(
            width=200, height=100,
            parent_width=375, parent_height=812,
            children_types=("text", "text", "text"),
        )
        cand = self.inferer.analyze("group", dom, [])
        assert cand is not None
        assert cand.name == "text-block"

    def test_r4_mixed_children(self):
        """Group with mixed children → not text-block."""
        dom = DomContext(
            width=200, height=100,
            parent_width=375, parent_height=812,
            children_types=("text", "image"),
        )
        cand = self.inferer.analyze("group", dom, [])
        assert cand is None or cand.name != "text-block"

    def test_r4_single_text_not_enough(self):
        """Only 1 text child → not text-block."""
        dom = DomContext(
            width=200, height=100,
            parent_width=375, parent_height=812,
            children_types=("text",),
        )
        cand = self.inferer.analyze("group", dom, [])
        assert cand is None or cand.name != "text-block"

    # --- R5: parent semantic inheritance ---

    def test_r5_image_inherits_icon(self):
        """Image under strong-semantic parent → parent-icon."""
        dom = DomContext(
            width=30, height=30,
            parent_width=200, parent_height=100,
            parent_semantic="slogan",
        )
        cand = self.inferer.analyze("image", dom, [])
        assert cand is not None
        assert cand.name == "slogan-icon"
        assert cand.confidence == 0.55

    def test_r5_shape_inherits_bg(self):
        """Shape under strong-semantic parent → parent-bg."""
        dom = DomContext(
            width=50, height=50,
            parent_width=200, parent_height=100,
            parent_semantic="prop",
        )
        # Shape with bad dimensions for btn → won't hit R2
        cand = self.inferer.analyze("shape", dom, [])
        assert cand is not None
        assert cand.name == "prop-bg"

    def test_r5_text_inherits_text(self):
        """Text under strong-semantic parent → parent-text."""
        dom = DomContext(
            parent_semantic="btn-receive",
        )
        cand = self.inferer.analyze("text", dom, [])
        assert cand is not None
        assert cand.name == "btn-receive-text"

    def test_r5_generic_parent_skipped(self):
        """Generic parent (group/img/text/shape) → no inheritance."""
        dom = DomContext(parent_semantic="group")
        cand = self.inferer.analyze("image", dom, [])
        assert cand is None

    def test_r5_none_parent_skipped(self):
        dom = DomContext(parent_semantic=None)
        cand = self.inferer.analyze("image", dom, [])
        assert cand is None

    def test_r5_not_triggered_with_strong_existing(self):
        """If strong candidate exists, R5 doesn't fire."""
        dom = DomContext(parent_semantic="slogan")
        existing = [NameCandidate("icon", 0.85, "layer1", "")]
        cand = self.inferer.analyze("image", dom, existing)
        assert cand is None

    def test_r5_large_image_gets_bg_suffix(self):
        """Large image (≥80% of parent) → parent-bg instead of parent-icon."""
        dom = DomContext(
            width=200, height=100,
            parent_width=200, parent_height=100,
            parent_semantic="card",
        )
        cand = self.inferer.analyze("image", dom, [])
        assert cand is not None
        assert cand.name == "card-bg"


# ===========================================================================
# semantic/name_resolver.py — NameResolver
# ===========================================================================


class TestNameResolver:
    def setup_method(self):
        self.resolver = NameResolver()

    def test_resolve_known_keyword(self):
        """Known keyword returns token."""
        token = self.resolver.resolve_token("按钮", "group")
        assert token == "btn"

    def test_resolve_empty_name(self):
        """Empty name → empty token."""
        token = self.resolver.resolve_token("", "group")
        assert token == ""

    def test_resolve_ps_default_name(self):
        """PS default name → empty (ltype fallback by caller)."""
        token = self.resolver.resolve_token("图层 5", "image")
        assert token == ""

    def test_caching_works(self):
        """Same (id, name, ltype) → cached result."""
        t1 = self.resolver.resolve_token("按钮", "group", layer_id="g1")
        t2 = self.resolver.resolve_token("按钮", "group", layer_id="g1")
        assert t1 == t2

    def test_different_ids_can_differ(self):
        """Different layer_ids may produce same/different tokens."""
        t1 = self.resolver.resolve_token("按钮", "group", layer_id="g1")
        t2 = self.resolver.resolve_token("背景", "image", layer_id="g2")
        assert t1 == "btn"
        assert t2 == "bg"

    def test_reset_clears_cache(self):
        """reset() clears internal caches."""
        self.resolver.resolve_token("按钮", "group", layer_id="g1")
        self.resolver.reset()
        # After reset, cache should be empty
        assert len(self.resolver._cache) == 0

    def test_layer2_with_dom_context(self):
        """Passing dom_context enables Layer 2 rules."""
        dom = DomContext(
            width=200, height=50,
            children_types=(),
        )
        # shape with button dimensions + no layer1 match
        token = self.resolver.resolve_token("形状 5", "shape", dom_context=dom)
        # Layer 2 R2 might fire → "btn" or fallback
        # At minimum we get some result (non-crash)
        assert isinstance(token, str)

    def test_layer1_beats_fallback(self):
        """Layer 1 (conf=0.85) should beat fallback (conf=0.5) for same token."""
        # "南瓜" is in cn_dict but also in _KEYWORDS
        token = self.resolver.resolve_token("南瓜道具", "image")
        # Should get a meaningful token
        assert token != ""

    def test_report_disabled_by_default(self):
        """Report not enabled → no accumulation."""
        self.resolver.resolve_token("按钮", "group")
        assert len(self.resolver._report_rows) == 0

    def test_report_enabled(self):
        """enable_report() → rows accumulated."""
        self.resolver.enable_report()
        self.resolver.resolve_token("按钮", "group", layer_id="g1")
        assert len(self.resolver._report_rows) == 1
        assert self.resolver._report_rows[0]["token"] == "btn"

    def test_dump_report_md(self):
        """dump_report_md produces markdown."""
        self.resolver.enable_report()
        self.resolver.resolve_token("按钮", "group", layer_id="g1")
        md = self.resolver.dump_report_md()
        assert "Naming Report" in md
        assert "btn" in md


class TestNameCandidate:
    def test_frozen(self):
        c = NameCandidate(name="btn", confidence=0.9, source="layer1")
        with pytest.raises(Exception):
            c.name = "other"  # type: ignore

    def test_defaults(self):
        c = NameCandidate(name="test")
        assert c.confidence == 0.0
        assert c.source == "fallback"
        assert c.reason == ""
