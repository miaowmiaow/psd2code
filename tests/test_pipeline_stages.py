"""Tests for target pipeline assembly — HTML / React / Vue.

Verifies stage composition, ordering, and naming conventions for each target
pipeline builder. Does NOT require real PSD files (mock-level only).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from framework.context import PipelineContext
from framework.pipeline import Pipeline
from framework.stage import Stage


# ===================================================================
# HTML Pipeline Stage Composition
# ===================================================================

class TestHtmlPipeline:
    """Verify build_html_pipeline returns proper stage sequence."""

    def _build(self):
        from targets.html.pipeline import build_html_pipeline
        ctx = PipelineContext(psd_path=Path("test.psd"))
        return build_html_pipeline(ctx)

    def test_stage_count(self):
        p = self._build()
        assert len(p.stages) == 5

    def test_stage_names(self):
        p = self._build()
        names = [s.name for s in p.stages]
        assert names == [
            "load_psd",
            "parse_to_ir",
            "html_codegen",
            "prune_pre_optimize",
            "layout_optimize",
        ]

    def test_stage_types(self):
        from targets.html.pipeline import (
            LoadPsdStage,
            ParseToIrStage,
            HtmlCodegenStage,
            PrunePreOptimizeStage,
            LayoutOptimizeStage,
        )
        p = self._build()
        stage_types = [type(s) for s in p.stages]
        assert stage_types == [
            LoadPsdStage,
            ParseToIrStage,
            HtmlCodegenStage,
            PrunePreOptimizeStage,
            LayoutOptimizeStage,
        ]

    def test_all_stages_are_stage_subclass(self):
        p = self._build()
        for s in p.stages:
            assert isinstance(s, Stage)


# ===================================================================
# React Pipeline Stage Composition
# ===================================================================

class TestReactPipeline:
    """Verify build_react_pipeline returns proper stage sequence."""

    def _build(self):
        from targets.react.pipeline import build_react_pipeline
        ctx = PipelineContext(psd_path=Path("test.psd"), target_name="react")
        return build_react_pipeline(ctx)

    def test_stage_count(self):
        p = self._build()
        # HTML 5 stages + 2 react stages = 7
        assert len(p.stages) == 7

    def test_shares_html_prefix(self):
        """First 5 stages should match HTML pipeline stages."""
        from targets.html.pipeline import build_html_pipeline
        ctx_html = PipelineContext(psd_path=Path("test.psd"))
        html_p = build_html_pipeline(ctx_html)
        react_p = self._build()
        html_names = [s.name for s in html_p.stages]
        react_prefix = [s.name for s in react_p.stages[:5]]
        assert react_prefix == html_names

    def test_react_specific_stages(self):
        p = self._build()
        react_stages = [s.name for s in p.stages[5:]]
        assert "html_to_react" in react_stages or len(react_stages) == 2

    def test_all_stages_are_stage_subclass(self):
        p = self._build()
        for s in p.stages:
            assert isinstance(s, Stage)


# ===================================================================
# Vue Pipeline Stage Composition
# ===================================================================

class TestVuePipeline:
    """Verify build_vue_pipeline returns proper stage sequence."""

    def _build(self):
        from targets.vue.pipeline import build_vue_pipeline
        ctx = PipelineContext(psd_path=Path("test.psd"), target_name="vue")
        return build_vue_pipeline(ctx)

    def test_stage_count(self):
        p = self._build()
        # HTML 5 stages + 2 vue stages = 7
        assert len(p.stages) == 7

    def test_shares_html_prefix(self):
        """First 5 stages should match HTML pipeline stages."""
        from targets.html.pipeline import build_html_pipeline
        ctx_html = PipelineContext(psd_path=Path("test.psd"))
        html_p = build_html_pipeline(ctx_html)
        vue_p = self._build()
        html_names = [s.name for s in html_p.stages]
        vue_prefix = [s.name for s in vue_p.stages[:5]]
        assert vue_prefix == html_names

    def test_vue_specific_stages(self):
        p = self._build()
        vue_stages = [s.name for s in p.stages[5:]]
        assert "html_to_vue" in vue_stages or len(vue_stages) == 2

    def test_all_stages_are_stage_subclass(self):
        p = self._build()
        for s in p.stages:
            assert isinstance(s, Stage)


# ===================================================================
# LoadPsdStage path normalization
# ===================================================================

class TestLoadPsdStage:
    """Test path normalization logic (without actual PSD I/O)."""

    def test_subdir_name_explicit(self):
        """Explicit subdir_name takes priority."""
        from targets.html.pipeline import LoadPsdStage
        stage = LoadPsdStage(subdir_name="html")
        assert stage._subdir_name == "html"

    def test_subdir_name_none_uses_target(self):
        from targets.html.pipeline import LoadPsdStage
        stage = LoadPsdStage()
        assert stage._subdir_name is None

    def test_stage_name(self):
        from targets.html.pipeline import LoadPsdStage
        stage = LoadPsdStage()
        assert stage.name == "load_psd"

    @patch("psd_tools.PSDImage.open")
    @patch("shutil.rmtree")
    def test_output_dir_structure(self, mock_rmtree, mock_open, tmp_path):
        """LoadPsdStage should set output_dir = base/stem/subdir."""
        from targets.html.pipeline import LoadPsdStage

        fake_psd = MagicMock()
        fake_psd.width = 375
        fake_psd.height = 812
        mock_open.return_value = fake_psd

        psd_path = tmp_path / "design.psd"
        psd_path.touch()
        base = tmp_path / "out"
        base.mkdir()

        ctx = PipelineContext(psd_path=psd_path, output_dir=base, target_name="html")
        stage = LoadPsdStage()

        with patch("config.Config.OUTPUT_BASE_DIR", str(base)):
            result = stage.run(ctx)

        assert result.output_dir == base / "design" / "html"
        assert result.project_root == base / "design"
        assert result.psd is fake_psd


# ===================================================================
# PrunePreOptimizeStage skip logic
# ===================================================================

class TestPrunePreOptimizeStage:
    """Test skip paths of PrunePreOptimizeStage."""

    def test_skip_no_html_path(self):
        from targets.html.pipeline import PrunePreOptimizeStage
        stage = PrunePreOptimizeStage()
        ctx = PipelineContext(psd_path=Path("x.psd"))
        # No html_path set → should gracefully skip
        result = stage.run(ctx)
        assert result is ctx

    def test_skip_missing_files(self, tmp_path):
        from targets.html.pipeline import PrunePreOptimizeStage
        stage = PrunePreOptimizeStage()
        ctx = PipelineContext(psd_path=Path("x.psd"))
        ctx.set("html_path", str(tmp_path / "nonexistent.html"))
        result = stage.run(ctx)
        assert result is ctx


# ===================================================================
# LayoutOptimizeStage skip logic
# ===================================================================

class TestLayoutOptimizeStage:
    """Test skip paths of LayoutOptimizeStage."""

    def test_skip_no_html_path(self):
        from targets.html.pipeline import LayoutOptimizeStage
        stage = LayoutOptimizeStage()
        ctx = PipelineContext(psd_path=Path("x.psd"))
        result = stage.run(ctx)
        assert result is ctx

    def test_skip_missing_files(self, tmp_path):
        from targets.html.pipeline import LayoutOptimizeStage
        stage = LayoutOptimizeStage()
        ctx = PipelineContext(psd_path=Path("x.psd"))
        ctx.set("html_path", str(tmp_path / "nonexistent.html"))
        result = stage.run(ctx)
        assert result is ctx


# ===================================================================
# Cross-target consistency
# ===================================================================

class TestCrossTargetConsistency:
    """Ensure HTML stages appear identically across all target pipelines."""

    def test_html_stages_shared(self):
        from targets.html.pipeline import build_html_pipeline, LoadPsdStage
        from targets.react.pipeline import build_react_pipeline
        from targets.vue.pipeline import build_vue_pipeline

        ctx = PipelineContext(psd_path=Path("t.psd"))
        html_p = build_html_pipeline(ctx)
        react_p = build_react_pipeline(PipelineContext(psd_path=Path("t.psd"), target_name="react"))
        vue_p = build_vue_pipeline(PipelineContext(psd_path=Path("t.psd"), target_name="vue"))

        # All three should share the same first 5 stage types
        html_types = [type(s).__name__ for s in html_p.stages]
        react_prefix = [type(s).__name__ for s in react_p.stages[:5]]
        vue_prefix = [type(s).__name__ for s in vue_p.stages[:5]]

        assert react_prefix == html_types
        assert vue_prefix == html_types

    def test_react_vue_have_extra_stages(self):
        from targets.react.pipeline import build_react_pipeline
        from targets.vue.pipeline import build_vue_pipeline

        react_p = build_react_pipeline(PipelineContext(psd_path=Path("t.psd"), target_name="react"))
        vue_p = build_vue_pipeline(PipelineContext(psd_path=Path("t.psd"), target_name="vue"))

        assert len(react_p.stages) > 5
        assert len(vue_p.stages) > 5
