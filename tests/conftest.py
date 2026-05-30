"""Shared pytest fixtures for psd2code tests."""

from __future__ import annotations

import pytest

from core.ir.styles import BBox, Style
from core.ir.nodes import GroupNode, ImageNode, TextNode, ShapeNode
from core.ir.assets import AssetRef
from core.ir.document import Document


# ---------------------------------------------------------------------------
# IR factory helpers
# ---------------------------------------------------------------------------

def _make_style(left: int = 0, top: int = 0, right: int = 100, bottom: int = 100, **kw):
    """Return a minimal Style with the given bbox."""
    return Style(bbox=BBox(left=left, top=top, right=right, bottom=bottom), **kw)


def _make_asset(src: str = "images/test.png", **kw) -> AssetRef:
    return AssetRef(src=src, **kw)


@pytest.fixture()
def minimal_style():
    """A 100×100 px style at the origin."""
    return _make_style()


@pytest.fixture()
def minimal_group():
    """An empty GroupNode."""
    return GroupNode(id="g1", name="root", style=_make_style())


@pytest.fixture()
def minimal_document():
    """A 375×812 Document with a single root GroupNode (no children)."""
    root = GroupNode(id="root", name="root", style=_make_style(0, 0, 375, 812))
    return Document(width=375, height=812, root=root)


@pytest.fixture()
def sample_document():
    """A small Document with a few children of different kinds."""
    root_style = _make_style(0, 0, 375, 812)
    child_img = ImageNode(
        id="img1", name="bg", style=_make_style(0, 0, 375, 200),
        asset=_make_asset("images/bg.png"),
    )
    child_text = TextNode(
        id="txt1", name="title", style=_make_style(20, 210, 355, 260),
        text="Hello World",
    )
    child_shape = ShapeNode(
        id="shp1", name="divider", style=_make_style(20, 270, 355, 272),
    )
    inner_group = GroupNode(
        id="g2", name="card", style=_make_style(20, 280, 355, 500),
        children=[
            ImageNode(
                id="img2", name="avatar", style=_make_style(30, 290, 80, 340),
                asset=_make_asset("images/avatar.png"),
            ),
        ],
    )
    root = GroupNode(
        id="root", name="root", style=root_style,
        children=[child_img, child_text, child_shape, inner_group],
    )
    return Document(width=375, height=812, root=root)


# ---------------------------------------------------------------------------
# CSS helpers
# ---------------------------------------------------------------------------

SAMPLE_CSS = """\
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  overflow: hidden;
}

#canvas {
  position: relative;
  width: 375px;
  height: 812px;
}

.bg__1 {
  position: absolute;
  left: 0px;
  top: 0px;
  width: 375px;
  height: 200px;
  background-image: url("images/bg.png");
  background-size: cover;
}

.title__2 {
  position: absolute;
  left: 20px;
  top: 210px;
  width: 335px;
  height: 50px;
  font-size: 22.099999999999998px;
  color: rgba(19, 12, 41, 1.0);
}

/* 分隔线 */
.divider__3 {
  position: absolute;
  left: 20px;
  top: 270px;
  width: 335px;
  height: 2px;
  background-color: #eee;
}
"""


@pytest.fixture()
def sample_css_text():
    return SAMPLE_CSS


@pytest.fixture()
def sample_css_rules():
    """Pre-parsed CSS rules dict (from SAMPLE_CSS)."""
    from common.css_utils import parse_css_to_dict
    return parse_css_to_dict(SAMPLE_CSS)
