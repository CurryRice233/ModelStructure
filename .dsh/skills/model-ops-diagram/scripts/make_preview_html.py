#!/usr/bin/env python3
"""Build a zoomable preview HTML from an mmdc-rendered SVG.

Usage:
  python3 make_preview_html.py --svg X.svg --out X_preview.html --title "模型结构图"

Rules (learned from a broken version — do not deviate):
  * Keep the SVG's original id (usually `my-svg`): the mermaid CSS inside the SVG
    is scoped to `#<id>` selectors, and renaming it kills all node/edge styles.
  * NEVER inject a second `id=` attribute — duplicate ids make the browser keep
    the first one, so `getElementById(<new id>)` returns null and the zoom
    script dies on load (buttons appear dead).
  * Only replace `width="100%"` with explicit `width`/`height` taken from the
    viewBox, then reference the SVG by its original id in the zoom script.
"""
import argparse
import re

TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  body { margin: 0; font-family: sans-serif; background: #f0f0f0; }
  #bar { position: sticky; top: 0; z-index: 9; background: #fff; padding: 8px 12px;
         border-bottom: 1px solid #ccc; }
  #bar button { margin-right: 8px; padding: 4px 12px; cursor: pointer; }
  #hint { color: #999; font-size: 12px; margin-left: 10px; }
  /* wrap 限制为视口高度：横向滚动条始终可见（图很高时否则沉在页底） */
  #wrap { overflow-x: scroll; overflow-y: auto; height: calc(100vh - 46px); }
  #__SVG_ID__ { background: #fff; display: block; }
  body.dragging, body.dragging * { cursor: grabbing !important; user-select: none; }
</style>
</head>
<body>
<div id="bar">
  <strong>__TITLE__</strong>
  <button onclick="setW('fit')">适合宽度</button>
  <button onclick="setW(0.5)">50%</button>
  <button onclick="setW(0.75)">75%</button>
  <button onclick="setW(1)">100%</button>
  <span id="info" style="color:#666"></span>
  <span id="hint">按住右键拖动可平移图示</span>
</div>
<div id="wrap">
__SVG__
</div>
<script>
  var VBW = __VBW__, VBH = __VBH__;
  var el = document.getElementById('__SVG_ID__');
  var wrap = document.getElementById('wrap');
  function setW(z) {
    if (!el) return;
    if (z === 'fit') {
      var w = wrap.clientWidth - 20;
      el.setAttribute('width', w); el.setAttribute('height', w * VBH / VBW);
      document.getElementById('info').textContent = '适合宽度 (' + Math.round(w / VBW * 100) + '%)';
    } else {
      el.setAttribute('width', VBW * z); el.setAttribute('height', VBH * z);
      document.getElementById('info').textContent = (z * 100) + '%';
    }
  }
  /* ---- 右键按住拖动平移 ---- */
  var drag = null;
  wrap.addEventListener('contextmenu', function (e) { e.preventDefault(); });
  wrap.addEventListener('mousedown', function (e) {
    if (e.button !== 2) return;   /* 仅右键 */
    e.preventDefault();
    drag = { x: e.clientX, y: e.clientY, sl: wrap.scrollLeft, st: wrap.scrollTop };
    document.body.classList.add('dragging');
  });
  window.addEventListener('mousemove', function (e) {
    if (!drag) return;
    wrap.scrollLeft = drag.sl - (e.clientX - drag.x);
    wrap.scrollTop = drag.st - (e.clientY - drag.y);
  });
  window.addEventListener('mouseup', function (e) {
    if (!drag || e.button !== 2) return;
    drag = null;
    document.body.classList.remove('dragging');
  });
  window.addEventListener('load', function () { setW('fit'); });
</script>
</body>
</html>
"""

def build(svg_path, out_path, title):
    raw = open(svg_path, encoding="utf-8").read()
    vb = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', raw)
    if not vb:
        raise SystemExit(f"no viewBox found in {svg_path}")
    vbw, vbh = vb.group(1), vb.group(2)
    idm = re.search(r'<svg[^>]*\bid="([^"]+)"', raw)
    svg_id = idm.group(1) if idm else "dsvg"
    svg = raw.replace('width="100%"', f'width="{vbw}" height="{vbh}"', 1)
    if svg.count(f'id="{svg_id}"') != raw.count(f'id="{svg_id}"'):
        raise SystemExit("unexpected id mutation")
    doc = (TEMPLATE.replace("__SVG__", svg)
           .replace("__TITLE__", title)
           .replace("__SVG_ID__", svg_id)
           .replace("__VBW__", vbw)
           .replace("__VBH__", vbh))
    open(out_path, "w", encoding="utf-8").write(doc)
    print(f"written: {out_path} (svg id={svg_id}, viewBox {vbw} x {vbh})")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--svg", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", required=True)
    a = ap.parse_args()
    build(a.svg, a.out, a.title)
