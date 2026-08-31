#!/usr/bin/env python3
"""Convert a rendered Mermaid flowchart (.mmd + mmdc-rendered .svg) into an Edraw .eddx file.

The eddx reproduces the mermaid layout 1:1:
  * node boxes  -> Process shapes (fill colors from the SVG inline styles)
  * subgraphs   -> dashed container shapes with the cluster title on top
  * edges       -> ConnectLine shapes using the renderer's `data-points` polylines
                   (base64 JSON on each <path data-id="L_...">), static coordinates
  * edge labels -> text block embedded in the connector at the SVG edgeLabel center

Text lines are taken from the .mmd source (authoritative line breaks), geometry from the SVG.

Usage:
  python3 make_eddx_from_mermaid.py --mmd X.mmd --svg X.svg \
      --skeleton base.eddx --out X.eddx [--margin 20] [--font-size 12]

The skeleton .eddx supplies the zip package parts (document.xml, theme.xml,
templates.xml, rels/*) and the page1.xml header. Any previously generated
diagram eddx works (e.g. agent_workspace/Qwen4ExpTextQSAIndexer_ops.eddx).

Notes / gotchas:
  * Edraw colors are ARGB `#ffRRGGBB` (alpha byte first). Strip the alpha byte
    when reusing them in SVG/HTML (otherwise wrong-hue translucent fills).
  * Works with the flowchart-v2 renderer output (mmdc >= mermaid 11.x): edges
    carry `data-points` and `data-id="L_<from>_<to>_<n>"`, edge labels carry the
    same data-id. Dotted edges (`-.->`) get class `edge-pattern-dotted`.
  * Connectors are NOT glued to shapes (static coordinates), so the geometry
    matches the mermaid render exactly; moving boxes in Edraw will not drag lines.
"""
import argparse
import base64
import json
import re
import zipfile
from html import unescape

# ---------------------------------------------------------------- mmd parsing
def parse_mmd(text):
    """Return (nodes, edges).

    nodes: id -> dict(lines=[...], cls=str|None)
    edges: list of dict(src, dst, label|None, dotted=bool, index=int)
           index = occurrence number within the same (src, dst) pair
    """
    nodes = {}
    # node: id["label"]:::class   (label may contain anything except an unescaped quote)
    for m in re.finditer(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\["((?:[^"\\]|\\.)*)"\](?:::([A-Za-z]+))?',
                         text, re.M):
        nid, label, cls = m.group(1), m.group(2), m.group(3)
        lines = [unescape(l) for l in re.split(r'<br\s*/?>', label)]
        nodes[nid] = dict(lines=lines, cls=cls)
    edges = []
    pair_count = {}
    for m in re.finditer(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(-->|-\.->)\s*'
                         r'(?:\|"([^"]*)"\|\s*)?([A-Za-z_][A-Za-z0-9_]*)', text, re.M):
        src, arrow, label, dst = m.groups()
        key = (src, dst)
        idx = pair_count.get(key, 0)
        pair_count[key] = idx + 1
        edges.append(dict(src=src, dst=dst, label=label,
                          dotted=(arrow == '-.->'), index=idx))
    return nodes, edges

# ---------------------------------------------------------------- svg parsing
def parse_svg(raw):
    """Return dict(nodes, clusters, edges)."""
    out = {}
    # nodes: <g class="node default matmul" id="PREFIX-flowchart-<id>-N" transform="translate(x, y)">
    nodes = {}
    node_re = re.compile(
        r'<g[^>]*class="node[^"]*"[^>]*id="[^"]*?flowchart-([A-Za-z_][A-Za-z0-9_]*)-\d+"'
        r'[^>]*transform="translate\(([-\d.]+),\s*([-\d.]+)\)"[^>]*>(.*?)(?=<g[^>]*class="node|<g class="cluster|$)',
        re.S)
    for m in node_re.finditer(raw):
        nid, tx, ty, body = m.group(1), float(m.group(2)), float(m.group(3)), m.group(4)
        r = re.search(r'<rect[^>]*x="([-\d.]+)"[^>]*y="([-\d.]+)"[^>]*width="([-\d.]+)"[^>]*height="([-\d.]+)"', body)
        if not r:
            continue
        x, y, w, h = (float(v) for v in r.groups())
        style = re.search(r'<rect[^>]*style="([^"]*)"', body)
        style = style.group(1) if style else ''
        fill = re.search(r'fill:(#[0-9a-fA-F]{6})', style)
        dash = 'stroke-dasharray' in style
        nodes[nid] = dict(cx=tx, cy=ty, w=w, h=h,
                          fill=fill.group(1) if fill else '#ffffff', dash=dash)
    out['nodes'] = nodes

    # clusters: <g class="cluster" id="PREFIX-<id>"> <rect style="..." x y w h/> ... cluster-label
    clusters = {}
    for m in re.finditer(r'<g class="cluster" id="[^"]*?-(\w+)"[^>]*>(.*?)(?=<g class="cluster"|<g class="edgePaths")',
                         raw, re.S):
        cid, body = m.group(1), m.group(2)
        r = re.search(r'<rect[^>]*x="([-\d.]+)"[^>]*y="([-\d.]+)"[^>]*width="([-\d.]+)"[^>]*height="([-\d.]+)"', body)
        if not r:
            continue
        x, y, w, h = (float(v) for v in r.groups())
        style = re.search(r'<rect[^>]*style="([^"]*)"', body)
        style = style.group(1) if style else ''
        fill_m = re.search(r'fill:(none|#[0-9a-fA-F]{6})', style)
        stroke_m = re.search(r'stroke:(#[0-9a-fA-F]{6})', style)
        dash = 'stroke-dasharray' in style
        title = None
        tl = re.search(r'<g class="cluster-label" transform="translate\(([-\d.]+),\s*([-\d.]+)\)".*?<p>(.*?)</p>',
                       body, re.S)
        if tl:
            title = unescape(re.sub(r'<[^>]+>', '', tl.group(3)))
        clusters[cid] = dict(x=x, y=y, w=w, h=h,
                             fill=(fill_m.group(1) if fill_m else '#ffffde'),
                             stroke=(stroke_m.group(1) if stroke_m else '#aaaa33'),
                             dash=dash, title=title,
                             tx=float(tl.group(1)) if tl else x + w / 2,
                             ty=float(tl.group(2)) if tl else y)
    out['clusters'] = clusters

    # edges: <path ... data-id="L_src_dst_N" data-points="b64" class="... dotted ...">
    edges = {}
    for m in re.finditer(r'<path ([^>]*)>', raw):
        tag = m.group(1)
        did = re.search(r'data-id="(L_[^"]+)"', tag)
        if not did:
            continue
        pts_b64 = re.search(r'data-points="([^"]+)"', tag)
        pts = json.loads(base64.b64decode(pts_b64.group(1))) if pts_b64 else []
        dotted = 'edge-pattern-dotted' in tag
        edges[did.group(1)] = dict(pts=[(p['x'], p['y']) for p in pts], dotted=dotted)
    out['edges'] = edges

    # edge label centers: outer <g class="edgeLabel" transform="translate(x, y)">
    labels = {}
    for m in re.finditer(r'<g class="edgeLabel" transform="translate\(([-\d.]+),\s*([-\d.]+)\)">'
                         r'<g class="label" data-id="(L_[^"]+)"', raw):
        labels[m.group(3)] = (float(m.group(1)), float(m.group(2)))
    out['edge_labels'] = labels
    return out

# ---------------------------------------------------------------- eddx building
def fmt(v):
    r = round(v, 4)
    if abs(r) < 1e-6:
        r = 0.0
    s = f'{r:.4f}'.rstrip('0').rstrip('.')
    return s if s not in ('-0', '') else '0'

def esc(t):
    return t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def argb(hex6):
    return '#ff' + hex6.lstrip('#')

def paras_xml(lines):
    return ''.join(f'\n                        <pp PX="0" CX="0"><tp CX="0">{esc(l)}</tp></pp>'
                   for l in lines)

def process_shape(sid, cx, cy, w, h, lines, fill, dash=False, align=4,
                  valign='Center', text_bk=False, line_color='#ff101843', font_size=12):
    hw, hh = w / 2, h / 2
    if fill is None:
        fill_xml = '<FillFormat Type="None"/>'
        nofill = '1'
    else:
        fill_xml = f'<FillFormat Type="Solid">\n                <Color V="{argb(fill)}"/>\n            </FillFormat>'
        nofill = '0'
    pattern = '9' if dash else '1'
    bk = ' BkColor="#ffffff"' if text_bk else ''
    quickmask = '307' if (fill is not None and fill.lower() != '#ffffff') else '51'
    return f'''    <Shape Type="Shape" ID="{sid}" Layer="2" NameU="Process" Name="\u6d41\u7a0b">
        <Transform>
            <Width V="{fmt(w)}"/>
            <Height V="{fmt(h)}"/>
            <Angle V="0"/>
            <GPinX V="{fmt(cx)}"/>
            <GPinY V="{fmt(cy)}"/>
            <LocPinX V="{fmt(hw)}" R="0.5" M="1"/>
            <LocPinY V="{fmt(hh)}" R="0.5" M="2"/>
            <FlipX V="0"/>
            <FlipY V="0"/>
        </Transform>
        <LevelData>
            <LibId V="4"/>
            <FromLibId V="4"/>
        </LevelData>
        <CPoints>
            <CPoint Name="Pt1" ID="1" Type="0">
                <X V="0"/>
                <Y V="{fmt(hh)}" R="0.5" M="2"/>
            </CPoint>
            <CPoint Name="Pt2" ID="2" Type="0">
                <X V="{fmt(hw)}" R="0.5" M="1"/>
                <Y V="0"/>
            </CPoint>
            <CPoint Name="Pt3" ID="3" Type="0">
                <X V="{fmt(w)}" R="1" M="1"/>
                <Y V="{fmt(hh)}" R="0.5" M="2"/>
            </CPoint>
            <CPoint Name="Pt4" ID="4" Type="0">
                <X V="{fmt(hw)}" R="0.5" M="1"/>
                <Y V="{fmt(h)}" R="1" M="2"/>
            </CPoint>
        </CPoints>
        <Misc>
            <ObjectType V="2"/>
        </Misc>
        <Events>
            <EventDrop V="0" F="0"/>
        </Events>
        <ShapeFormat QuickMask="{quickmask}">
            {fill_xml}
            <LineFormat>
                <LineWeight V="1"/>
                <LineCap V="Flat"/>
                <LinePattern ID="{pattern}"/>
                <BeginArrow ID="0" Size="4"/>
                <EndArrow ID="0" Size="4"/>
                <CompoundType V="0"/>
                <LineFill Type="Solid">
                    <Color V="{line_color}"/>
                </LineFill>
            </LineFormat>
            <EffectFormat>
                <Shadow Mode="0"/>
            </EffectFormat>
        </ShapeFormat>
        <Texts>
            <Text ID="1" Name="T1" ExtendMode="0">
                <Transform>
                    <Width V="{fmt(w)}" R="1" M="1"/>
                    <Height V="{fmt(h)}" R="1" M="2"/>
                    <Angle V="0"/>
                    <GPinX V="{fmt(hw)}" R="0.5" M="1"/>
                    <GPinY V="{fmt(hh)}" R="0.5" M="2"/>
                    <LocPinX V="{fmt(hw)}" F="Text.T1.Width*0.5"/>
                    <LocPinY V="{fmt(hh)}" F="Text.T1.Height*0.5"/>
                    <TxtField V="" U="STR"/>
                </Transform>
                <TextBlock VAlign="{valign}" TextFormatMask="0" TabStop="80">
                    <Color V="#ff191919"/>
                    <Character IX="0" Family="Microsoft YaHei" Size="{font_size}" Color="#191919"{bk}/>
                    <Paragraph IX="0" SpLine="100" Align="{align}" IndFirst="0" IndLeft="0" IndRight="0" SpaceBefore="0" SpaceAfter="0"/>
                    <Margins Left="4" Right="4" Top="4" Bottom="4"/>
                    <Text>{paras_xml(lines)}
                    </Text>
                </TextBlock>
            </Text>
        </Texts>
        <Geometries>
            <Geometry>
                <NoFill V="{nofill}"/>
                <NoLine V="0"/>
                <Closed V="1"/>
                <NoShow V="0"/>
                <NoSnap V="1"/>
                <MoveTo>
                    <X V="{fmt(w)}" R="1" M="1"/>
                    <Y V="{fmt(h)}" R="1" M="2"/>
                </MoveTo>
                <LineTo>
                    <X V="{fmt(w)}" R="1" M="1"/>
                    <Y V="0"/>
                </LineTo>
                <LineTo>
                    <X V="0"/>
                    <Y V="0"/>
                </LineTo>
                <LineTo>
                    <X V="0"/>
                    <Y V="{fmt(h)}" R="1" M="2"/>
                </LineTo>
                <LineTo>
                    <X V="{fmt(w)}" R="1" M="1"/>
                    <Y V="{fmt(h)}" R="1" M="2"/>
                </LineTo>
            </Geometry>
        </Geometries>
        <TextAdjustSize V="0"/>
        <WordWrap V="TRUE"/>
    </Shape>'''

def connect_shape(cid, pts, label, lpos, dash=False, font_size=12):
    """Static (un-glued) connector through absolute polyline points."""
    bx, by = pts[0]
    ex, ey = pts[-1]
    W, H = ex - bx, ey - by
    geo = ['''                <MoveTo>
                    <X V="0"/>
                    <Y V="0"/>
                </MoveTo>''']
    for (px, py) in pts[1:]:
        geo.append(f'''                <LineTo>
                    <X V="{fmt(px - bx)}"/>
                    <Y V="{fmt(py - by)}"/>
                </LineTo>''')
    geo_xml = '\n'.join(geo)
    pattern = '9' if dash else '1'
    if label:
        lx, ly = lpos[0] - bx, lpos[1] - by
        texts_xml = f'''        <Texts>
            <Text ID="1" Name="T1" ExtendMode="2">
                <Transform>
                    <Width V="96" F="GUARD(TEXTWIDTH(Text.T1))"/>
                    <Height V="27" F="GUARD(TEXTHEIGHT(Text.T1))"/>
                    <Angle V="0"/>
                    <GPinX V="{fmt(lx)}"/>
                    <GPinY V="{fmt(ly)}"/>
                    <LocPinX V="48" F="Text.T1.Width*0.5"/>
                    <LocPinY V="13.5" F="Text.T1.Height*0.5"/>
                    <TxtField V="" U="STR"/>
                </Transform>
                <TextBlock VAlign="Center" TextFormatMask="0" TabStop="80">
                    <Color V="#ff191919"/>
                    <Character IX="0" Family="Microsoft YaHei" Size="{font_size}" Color="#191919" BkColor="#ffffff"/>
                    <Paragraph IX="0" SpLine="100" Align="4" IndFirst="0" IndLeft="0" IndRight="0" SpaceBefore="0" SpaceAfter="0"/>
                    <Margins Left="4" Right="4" Top="4" Bottom="4"/>
                    <Text>
                        <pp PX="0" CX="0"><tp CX="0">{esc(label)}</tp></pp>
                    </Text>
                </TextBlock>
            </Text>
        </Texts>
'''
    else:
        texts_xml = ''
    return f'''    <Shape Type="ConnectLine" ID="{cid}" Layer="2" NameU="ConnectLine" Name="ConnectLine">
        <ConPoints>
            <BeginX V="{fmt(bx)}"/>
            <BeginY V="{fmt(by)}"/>
            <EndX V="{fmt(ex)}"/>
            <EndY V="{fmt(ey)}"/>
        </ConPoints>
        <Transform>
            <Width V="{fmt(W)}"/>
            <Height V="{fmt(H)}"/>
            <Angle V="0"/>
            <GPinX V="{fmt((bx + ex) / 2)}"/>
            <GPinY V="{fmt((by + ey) / 2)}"/>
            <LocPinX V="{fmt(W / 2)}" R="0.5" M="1"/>
            <LocPinY V="{fmt(H / 2)}" R="0.5" M="2"/>
            <FlipX V="0"/>
            <FlipY V="0"/>
        </Transform>
        <Misc/>
        <ShapeFormat QuickMask="99">
            <FillFormat Type="None"/>
            <LineFormat>
                <LineWeight V="1"/>
                <LineCap V="Flat"/>
                <LinePattern ID="{pattern}"/>
                <BeginArrow ID="0" Size="3"/>
                <EndArrow ID="4" Size="4"/>
                <CompoundType V="0"/>
                <LineFill Type="Solid">
                    <Color V="#ff333333"/>
                </LineFill>
            </LineFormat>
            <EffectFormat>
                <Shadow Mode="0"/>
            </EffectFormat>
        </ShapeFormat>
{texts_xml}        <Geometries>
            <Geometry>
                <NoFill V="1"/>
                <NoLine V="0"/>
                <Closed V="0"/>
                <NoShow V="0"/>
                <NoSnap V="1"/>
{geo_xml}
            </Geometry>
        </Geometries>
        <TextAdjustSize V="0"/>
        <WordWrap V="TRUE"/>
        <ConnectorLayout Relayout="FALSE" IgnoreJump="FALSE" Style="2"/>
    </Shape>'''

# ---------------------------------------------------------------- main
def build(mmd_path, svg_path, skeleton_path, out_path, margin=20.0, font_size=12):
    nodes_mmd, edges_mmd = parse_mmd(open(mmd_path, encoding='utf-8').read())
    svg = parse_svg(open(svg_path, encoding='utf-8').read())

    # coordinate bounds -> page size; shift everything by (margin - min)
    xs, ys = [], []
    for n in svg['nodes'].values():
        xs += [n['cx'] - n['w'] / 2, n['cx'] + n['w'] / 2]
        ys += [n['cy'] - n['h'] / 2, n['cy'] + n['h'] / 2]
    for c in svg['clusters'].values():
        xs += [c['x'], c['x'] + c['w']]
        ys += [c['y'], c['y'] + c['h']]
    for e in svg['edges'].values():
        xs += [p[0] for p in e['pts']]
        ys += [p[1] for p in e['pts']]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    dx, dy = margin - minx, margin - miny
    page_w = (maxx - minx) + 2 * margin
    page_h = (maxy - miny) + 2 * margin

    def T(pt):
        return (pt[0] + dx, pt[1] + dy)

    shapes = []
    sid = 200  # clusters first (z-order bottom)
    for cid, c in svg['clusters'].items():
        fill = None if c['fill'] == 'none' else c['fill']
        shapes.append(process_shape(sid, c['x'] + c['w'] / 2 + dx, c['y'] + c['h'] / 2 + dy,
                                    c['w'], c['h'], [c['title'] or cid], fill=fill,
                                    dash=c['dash'], align=4, valign='Top', text_bk=True,
                                    line_color=argb(c['stroke']), font_size=font_size))
        sid += 1

    sid = 300  # node boxes
    for nid, n in svg['nodes'].items():
        info = nodes_mmd.get(nid)
        lines = info['lines'] if info else [nid]
        shapes.append(process_shape(sid, n['cx'] + dx, n['cy'] + dy, n['w'], n['h'],
                                    lines, fill=n['fill'], dash=n['dash'],
                                    align=4, valign='Center', font_size=font_size))
        sid += 1

    cid = 600  # connectors
    for e in edges_mmd:
        eid = f"L_{e['src']}_{e['dst']}_{e['index']}"
        geo = svg['edges'].get(eid)
        if geo is None or not geo['pts']:
            print(f'  ! edge {eid} has no SVG geometry, skipped')
            continue
        pts = [T(p) for p in geo['pts']]
        lpos = svg['edge_labels'].get(eid)
        lpos = T(lpos) if lpos else None
        label = e['label'] if e['label'] else None
        if label and lpos is None:
            mid = pts[len(pts) // 2]
            lpos = mid
        shapes.append(connect_shape(cid, pts, label, lpos,
                                    dash=e['dotted'] or geo['dotted'], font_size=font_size))
        cid += 1

    # assemble page1.xml from the skeleton header
    zin = zipfile.ZipFile(skeleton_path)
    page = zin.read('pages/page1.xml').decode('utf-8')
    cut = page.find('<Shape Type=')
    if cut < 0:
        cut = page.rfind('</Page>')
    head = page[:cut]
    head = re.sub(r'(<PageProps>\s*<Width V=")[^"]*(")', rf'\g<1>{fmt(page_w)}\2', head, count=1)
    head = re.sub(r'(<PageProps>\s*<Width[^>]*/>\s*<Height V=")[^"]*(")', rf'\g<1>{fmt(page_h)}\2', head, count=1)
    page_new = head + '\n'.join(shapes) + '\n    <Connects>\n    </Connects>\n</Page>\n'

    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        for item in zin.namelist():
            if item == 'pages/page1.xml' or item.endswith('thumbnail.jpeg'):
                continue
            zout.writestr(item, zin.read(item))
        zout.writestr('pages/page1.xml', page_new.encode('utf-8'))

    n_clusters = len(svg['clusters'])
    n_nodes = len(svg['nodes'])
    n_conn = cid - 600
    print(f'written: {out_path}')
    print(f'  page {fmt(page_w)} x {fmt(page_h)}, shapes: {n_clusters} clusters + '
          f'{n_nodes} boxes + {n_conn} connectors')
    return out_path

def check(eddx_path):
    """Mechanical sanity check: well-formed XML, counts, colors, bounds."""
    import xml.dom.minidom as minidom
    z = zipfile.ZipFile(eddx_path)
    page = z.read('pages/page1.xml').decode('utf-8')
    minidom.parseString(page)  # raises if malformed
    shapes = re.findall(r'<Shape Type="(\w+)"', page)
    fills = re.findall(r'<FillFormat Type="Solid">\s*<Color V="(#[0-9a-f]{8})"', page)
    from collections import Counter
    print(f'  check: XML well-formed; shapes={Counter(shapes)}')
    print(f'  check: fills={Counter(fills)}')
    pw = re.search(r'<PageProps>\s*<Width V="([\d.]+)"/>', page)
    ph = re.search(r'<Height V="([\d.]+)"/>', page)
    print(f'  check: page {pw.group(1)} x {ph.group(1)}')

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--mmd', required=True)
    ap.add_argument('--svg', required=True)
    ap.add_argument('--skeleton', required=True, help='base .eddx providing the zip package parts')
    ap.add_argument('--out', required=True)
    ap.add_argument('--margin', type=float, default=20.0)
    ap.add_argument('--font-size', type=int, default=12)
    a = ap.parse_args()
    build(a.mmd, a.svg, a.skeleton, a.out, a.margin, a.font_size)
    check(a.out)
