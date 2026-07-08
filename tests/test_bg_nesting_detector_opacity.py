from bs4 import BeautifulSoup

from targets.html.postprocess.bg_nesting_restructure.detector import (
    detect_image_absorption_hosts,
    is_background_leaf,
)
from targets.html.postprocess.bg_nesting_restructure.restructurer import (
    restructure_by_bg_nesting,
)


def test_is_background_leaf_rejects_non_opaque_candidate():
    html = """
    <div id="parent" style="left:0px; top:0px; width:300px; height:300px;">
      <div class="bg" data-type="image" style="left:0px; top:0px; width:300px; height:300px; z-index:10; opacity:0.58;"></div>
      <div class="fg" data-type="text" style="left:20px; top:20px; width:100px; height:40px; z-index:11;"></div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    parent = soup.find(id="parent")
    assert parent is not None

    children = [c for c in parent.children if getattr(c, "name", None)]
    bg = children[0]

    assert not is_background_leaf(bg, parent, children)


def test_is_background_leaf_accepts_opacity_one():
    html = """
    <div id="parent" style="left:0px; top:0px; width:300px; height:300px;">
      <div class="bg" data-type="image" style="left:0px; top:0px; width:300px; height:300px; z-index:10; opacity:1;"></div>
      <div class="fg" data-type="text" style="left:20px; top:20px; width:100px; height:40px; z-index:11;"></div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    parent = soup.find(id="parent")
    assert parent is not None

    children = [c for c in parent.children if getattr(c, "name", None)]
    bg = children[0]

    assert is_background_leaf(bg, parent, children)


def test_detect_image_absorption_hosts_skips_non_opaque_image_host():
    html = """
    <div id="parent" style="left:0px; top:0px; width:300px; height:300px;">
      <div class="host" data-type="image" style="left:20px; top:20px; width:200px; height:200px; z-index:1; opacity:0.58;"></div>
      <div class="a" data-type="text" style="left:40px; top:40px; width:60px; height:30px; z-index:2;"></div>
      <div class="b" data-type="text" style="left:120px; top:120px; width:60px; height:30px; z-index:3;"></div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    parent = soup.find(id="parent")
    assert parent is not None

    hosts = detect_image_absorption_hosts(parent)
    assert len(hosts) == 0


def test_detect_image_absorption_hosts_accepts_opaque_image_host():
    html = """
    <div id="parent" style="left:0px; top:0px; width:300px; height:300px;">
      <div class="host" data-type="image" style="left:20px; top:20px; width:200px; height:200px; z-index:1; opacity:1;"></div>
      <div class="a" data-type="text" style="left:40px; top:40px; width:60px; height:30px; z-index:2;"></div>
      <div class="b" data-type="text" style="left:120px; top:120px; width:60px; height:30px; z-index:3;"></div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    parent = soup.find(id="parent")
    assert parent is not None

    hosts = detect_image_absorption_hosts(parent)
    assert len(hosts) == 1


def test_detect_image_absorption_hosts_accepts_single_text_child_button_case():
    html = """
    <div id="parent" style="left:0px; top:0px; width:180px; height:280px;">
      <div class="title" data-type="text" style="left:40px; top:20px; width:90px; height:20px; z-index:60;"></div>
      <div class="btn-bg" data-type="image" style="left:20px; top:180px; width:140px; height:50px; z-index:63; opacity:1;"></div>
      <div class="btn-text" data-type="text" style="left:55px; top:194px; width:70px; height:22px; z-index:64;"></div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    parent = soup.find(id="parent")
    assert parent is not None

    hosts = detect_image_absorption_hosts(parent)
    assert len(hosts) == 1
    assert hosts[0].get("class") == ["btn-bg"]


def test_restructure_allows_non_opaque_nodes_to_be_nested_but_not_hosts():
    html = """
    <div id="canvas" style="left:0px; top:0px; width:300px; height:300px;">
      <div class="bg" data-name="背景" data-type="image"></div>
      <div class="overlay" data-name="图层 1" data-type="image"></div>
      <div class="txt" data-name="文案" data-type="text"></div>
    </div>
    """
    css = {
        ".bg": {
            "left": "0px",
            "top": "0px",
            "width": "300px",
            "height": "300px",
            "z-index": "1",
            "opacity": "1",
        },
        ".overlay": {
            "left": "0px",
            "top": "0px",
            "width": "300px",
            "height": "300px",
            "z-index": "2",
            "opacity": "0.58",
        },
        ".txt": {
            "left": "20px",
            "top": "20px",
            "width": "100px",
            "height": "30px",
            "z-index": "3",
            "opacity": "1",
        },
    }

    out = restructure_by_bg_nesting(html, css_rules=css)
    soup = BeautifulSoup(out, "html.parser")

    overlay = soup.find(attrs={"data-name": "图层 1"})
    assert overlay is not None
    # 半透明图层可以被重组（被吸收到不透明背景中）
    assert overlay.has_attr("data-bg-nested")

    txt = soup.find(attrs={"data-name": "文案"})
    assert txt is not None
    # 兄弟文案应吸收到背景，不应吸收到半透明 overlay
    assert txt.parent is not None
    assert txt.parent.get("data-name") == "背景"
