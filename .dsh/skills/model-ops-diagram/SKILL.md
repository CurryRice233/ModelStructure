---
name: model-ops-diagram
description: Use when asked to draw an operator-level dataflow/structure diagram of a neural-network module (attention, indexer, MLP, MoE, cache update, etc.) from a model's config.json and modeling source file. Produces a shape-annotated Mermaid flowchart — boxes are operators, second line is the weight/output shape in [name(value), ...] format, edge labels are flowing tensor shapes, matmul boxes light green, RMSNorm/SiLU-style ops light blue — then renders SVG + PNG with the local mmdc pipeline and validates without viewing images. On request it also converts the rendered diagram into an editable Edraw .eddx with the same layout, and publishes the artifacts into a per-model folder listed in the workspace root index.html gallery.
whenToUse: Trigger on requests like "画出/重画 X 模块的算子级结构图/数据流图（mermaid）", "给我 config 和 modeling 代码，输出算子图", or follow-up style tweaks to such a diagram (shape naming, colors, value annotations).
---

# Model Operator-Level Diagram: config + modeling code → Mermaid + PNG

Inputs: the model's **config.json** (file, path, or URL) and the **modeling source** (file or URL), plus the target module/class name (ask if ambiguous). Outputs: `<name>.mmd`, `<name>.svg`, `<name>.png` in the session workspace (optionally an editable Edraw `<name>.eddx` of the same layout, a zoomable preview HTML, optionally copied where the user keeps documents).

## Step 1 — Extract concrete values from the config

Parse the JSON (fetch with `curl -sL` if URL; HuggingFace: use `resolve/main`, not `blob/main`). Multimodal configs nest the text model under `text_config` — descend into it. Collect every dimension the target module touches: `hidden_size`, `head_dim`, head counts, `rms_norm_eps`, module-specific params, `partial_rotary_factor` (rotary dim = `head_dim × partial_rotary_factor`), layer layout (`layer_types`, intervals), etc. Gated repos: try `hf-mirror.com` and ModelScope; if still unavailable, keep those dims symbolic and say so explicitly.

## Step 2 — Derive the operator flow from the modeling code

Read the target module's `__init__` (weights + their shapes, bias, eps) and `forward()` in source order. Rules:

- One box = one operator, or several trivially simple consecutive ops joined with `+` in the title (e.g. `split + reshape + squeeze`, `where + scatter + slice`).
- Track every intermediate tensor shape symbolically (`B`, `S`, `K`, `Nv`, …), substituting concrete config values where they apply.
- Derive computed constants and show formula with result, e.g. `(nq(4)+1)·Id(128) = 640`, `Bᵢ(2048)+R(4)−1 = 2051`.
- Mark data-dependent loops (per-batch/per-query, per-layer) for a `subgraph` container.

## Step 3 — Mermaid style rules (fixed)

```
flowchart TD
    classDef matmul fill:#90d6ab,stroke:#101843,stroke-width:1px,color:#191919
    classDef act fill:#83ebdc,stroke:#101843,stroke-width:1px,color:#191919
    classDef plain fill:#ffffff,stroke:#101843,stroke-width:1px,color:#191919
    classDef dashed fill:#ffffff,stroke:#101843,stroke-width:1px,stroke-dasharray:5 5,color:#191919
```

- Box: `id["operator title<br/>shape line"]`. Line 1 = operator name(s); line 2 = the shape (weights for weighted ops — include `eps(...)`, `bias(False)` etc.; output shape for weightless ops). More lines only when an op produces several tensors.
- Shape format: `[dim, dim, ...]`. **Every dimension whose value is known is written `name(value)`** — e.g. `[B, S, H(2560)]`, `[Id(128)], eps(1e-6)`. Runtime dims stay symbolic.
- **Single shape → omit the tensor name** (`[nb, Id(128)]`); multiple shapes in one box/edge → prefix each with its tensor name (`q [B, S, nq(4), Id(128)]`).
- Edge label = shape of the tensor flowing on that edge: `A -->|"[B, S, 640]"| B`. One edge = one tensor = name omitted (same rule); an edge carrying two tensors keeps both names.
- Colors: `:::matmul` for any box containing a matmul (Linear or activation matmul); `:::act` for RMSNorm/LayerNorm/SiLU/ReLU/sigmoid/softmax-style common ops; `:::plain` otherwise; `:::dashed` for "outside this module" terminal boxes.
- Loop container: `subgraph loop["for ... — 说明"]` … `end` plus `style loop fill:none,stroke:#4155c6,stroke-width:1px,stroke-dasharray:5 5,color:#191919`.
- Start the file with `%%` comments recording value provenance (config URL + extracted numbers).
- Chinese labels are fine; avoid `"` inside labels.

## Step 4 — Render (this machine's pipeline)

```bash
cd /home/jiali/codes/agent_workspace
mermaid-render/node_modules/.bin/mmdc -i X.mmd -o X.svg -p mermaid-render/puppeteer-config.json -b white
mermaid-render/node_modules/.bin/mmdc -i X.mmd -o X.png -p mermaid-render/puppeteer-config.json -b white -s 2
```

`mermaid-render/puppeteer-config.json` points at system Chrome (`/usr/bin/google-chrome`, `--no-sandbox`, headless:new). If mmdc is missing, reinstall with `npm install @mermaid-js/mermaid-cli --cache /home/jiali/codes/agent_workspace/.npm-cache` inside `mermaid-render/` (`~/.npm` is read-only). Plain packages like PlantUML jars come from the Aliyun Maven mirror when GitHub times out.

## Step 5 — Validate without viewing images

The model cannot see rendered pictures; verify mechanically:

1. HTML-entity-decode the SVG and assert every expected string exists (all box titles, all shape lines, all edge labels, concrete values).
2. Count classDef fills in the SVG source (green = matmul box count, blue = act box count).
3. Parse node geometry: regex `<g class="node[^"]*" id="...-([A-Za-z_]+)-\d+" ... transform="translate\(x, y\)"` + inner `<rect x y width height>`; assert no two bounding boxes overlap.
4. Optional pixel probe: screenshot the SVG with headless Chrome at known window size, decode the PNG with a pure-Python zlib/unfilter decoder (no PIL/numpy available), compare fills at node centers with ±15 tolerance; probe several x-offsets because centers can land on text glyphs.

## Step 6 — Edraw .eddx of the same diagram (on request)

When the user also wants an editable Edraw file ("再生成同样的 eddx"), convert the already-rendered pair with the skill's converter script (path relative to this skill's base directory):

```bash
python3 <skill_dir>/scripts/make_eddx_from_mermaid.py \
    --mmd X.mmd --svg X.svg \
    --skeleton /home/jiali/codes/agent_workspace/Qwen4ExpTextQSAIndexer_ops.eddx \
    --out X.eddx
```

How it works and what to know:

- Geometry comes from the rendered SVG, 1:1 with the PNG layout: node boxes from `g.node` (translate + rect + inline `fill:`/`stroke-dasharray` styles), subgraphs from `g.cluster` (default clusters keep mermaid's `#ffffde`/`#aaaa33`; `style ... fill:none` clusters become no-fill dashed containers), edges from the flowchart-v2 renderer's base64 `data-points` polylines on each `<path data-id="L_<from>_<to>_<n>">`. Edge labels are matched to edges via the same `data-id` (index = declaration order in the .mmd for duplicate pairs). `-.->` edges carry class `edge-pattern-dotted` → LinePattern 9.
- Text lines come from the **.mmd source** (authoritative `<br/>` line breaks — mermaid may visually wrap long lines, so never parse box text back out of the SVG). Font size defaults to 12pt (= mermaid's 16px CSS), `--font-size` to change.
- Nodes → `Process` shapes; clusters → dashed `Process` containers drawn first (z-order bottom) with the title top-center on a white text background; edges → `ConnectLine` shapes with **static coordinates** (no glue formulas), so the geometry matches the render exactly — moving a box in Edraw will not drag its lines.
- The skeleton .eddx only supplies the zip package parts (`document.xml`, `theme.xml`, `templates.xml`, `rels/*`) and the `pages/page1.xml` header; any previously generated eddx works. The user's hand-edited originals live under `/mnt/d/Docs/` (e.g. `Qwen4ExpTextQSAIndexer.eddx`).
- Edraw colors are ARGB `#ffRRGGBB`: matmul `#ff90d6ab`, act `#ff83ebdc`, plain/dashed `#ffffffff`, connector lines `#ff333333`.
- Validate: the script re-parses the written zip (XML well-formed, shape/fill counts); additionally assert that every node line and edge label from the .mmd appears in entity-decoded `pages/page1.xml`, and that every connector point lies inside the page bounds.

## Step 7 — Deliver

Write `.mmd`, `.svg`, `.png` (and `.eddx` when requested) to the workspace and name them in the final reply as inline code. Zoomable preview HTML: generate it with the skill's `scripts/make_preview_html.py --svg X.svg --out {model_slug}_structure_preview.html --title "…"` (inlines the SVG, explicit `width`/`height` from the viewBox; features: fit/50%/75%/100% buttons, `#wrap` clamped to viewport height so the horizontal scrollbar is always reachable on very tall diagrams, and right-button drag panning with the context menu suppressed inside the canvas). Two hard rules it enforces: keep the SVG's original `id` (mermaid's CSS is scoped to `#my-svg` selectors), and never inject a second `id=` attribute — duplicate ids make the browser keep the first one, so the zoom script's `getElementById` returns null and the buttons silently die. Then archive + publish everything per Step 8. If the user keeps documents outside the workspace (e.g. `/mnt/d/Docs/`), copy there — the sandbox denies it under workspace-write, so retry the same `cp` once with `sandbox_permissions` + a one-sentence justification.

## Step 8 — Publish to the gallery index (index.html)

The workspace root keeps an `index.html` listing every model's diagram files, grouped by **series / model name**, rendered as collapsible cards (click a model to expand its file list). Publishing is mandatory after every diagram:

1. **Archive artifacts** under `<workspace>/<Series>/<ModelName>/`, where Series/ModelName come from the HuggingFace repo id — e.g. `Qwen/Qwen3.8-Flash-Next/` for `Qwen3.8-Flash-Next`. **File naming convention: `{model_slug}_structure.{ext}`**, where the slug is the model name lowercased with dots dropped and dashes turned into underscores — `Qwen3.8-Flash-Next` → `qwen38_flash_next_structure.{mmd,svg,png,eddx}`, `GLM-5.3-Flash` → `glm53_flash_structure.{…}`; the preview is `{model_slug}_structure_preview.html`. Move the artifacts there (config/modeling reference files may live alongside).
2. **Update the root `index.html`**. It is data-driven: the only thing to maintain is the JS array `MODELS` inside the marker block (`/* ==== MODELS ... ====` … `/* ==== /MODELS ====`); the page renders itself from it.
   - If `index.html` does not exist yet, copy the skill's `scripts/index_template.html` to the workspace root first.
   - Model already listed → append the new file(s) to its `files` array (same `name` = regeneration → replace that entry).
   - New model → append one object:
     ```js
     {
       series: "Qwen",
       model: "Qwen3.8-Flash-Next",
       dir: "Qwen/Qwen3.8-Flash-Next",
       files: [
         { type: "html", name: "qwen38_flash_next_structure_preview.html", desc: "可缩放预览" },
         { type: "svg",  name: "qwen38_flash_next_structure.svg",          desc: "矢量算子级结构图" },
         { type: "png",  name: "qwen38_flash_next_structure.png",          desc: "高清位图" },
         { type: "eddx", name: "qwen38_flash_next_structure.eddx",         desc: "Edraw 可编辑文件" },
         { type: "mmd",  name: "qwen38_flash_next_structure.mmd",          desc: "Mermaid 源码" },
       ]
     },
     ```
   - Entry fields: `type ∈ svg | png | eddx | mmd | html | other` (picks the badge style), `name` relative to `dir`; the link is `dir + "/" + name` and opens in a new tab.
3. **Validate**: parse `index.html`, and assert every `dir/name` path exists on disk before reporting done.

## Environment gotchas

- Working dir is `/home/jiali/codes/agent_workspace`; the harness checkout path is unrelated to cwd.
- `/tmp` does not persist between bash calls; keep intermediates in the workspace.
- HF gated repos return "Invalid username or password" on `resolve/main`.
- Chrome screenshot size ≠ SVG coordinate space: map through `viewBox` (`scale = png_width / viewBox_width`).
- mmdc fits the diagram into a default ~800px viewport: tall/wide diagrams come out as a shrunken PNG with unreadable text. Re-render with `-w <about half the viewBox width>` (check `viewBox` in the SVG root) so the PNG stays ~1:1.
- In SVG/CSS, 8-digit hex is RRGGBBAA — Edraw-style ARGB colors (`#ff90d6ab`) render as translucent wrong-hue fills; strip the alpha byte for SVG. Edraw wants the ARGB form back (`#ff` + 6-digit RGB).
- `index.html` must stay machine-editable: only touch the `MODELS` array between the marker comments; never hand-add markup outside it, and keep entries in the exact `{series, model, dir, files}` shape.
- Preview HTML zoom buttons dead? Almost always a duplicate `id` on the inlined `<svg>` tag (`id="my-svg" id="dsvg"`): HTML keeps the first id, so `getElementById` for the second returns null. Regenerate with `scripts/make_preview_html.py` and verify by injecting `setW(0.5)` on load in a temp copy and dumping the DOM with headless Chrome — the svg's `width`/`height` attributes must change.

## Worked example (Qwen3.8-Flash-Next QSA indexer)

Config `text_config`: hidden_size 2560, indexer_n_heads 4, indexer_kv_heads 1, indexer_head_dim 128, indexer_budget 2048, indexer_compress_ratio 4 (Bₜ=512), head_dim 256 × partial_rotary_factor 0.25 = rot 64, eps 1e-6. Resulting fragment:

```
qk_proj["index_qk_proj (Linear)<br/>[(nq(4)+1)·Id(128) = 640, H(2560)]<br/>bias(False)"]:::matmul
q_ln["q_layernorm (RMSNorm)<br/>[Id(128)], eps(1e-6)"]:::act
split -->|"[B, S, 640]"| qk_proj_downstream   %% one tensor → no name
```

Reference artifacts in the workspace: `qsa_ops.mmd`, `make_qsa_ops_diagram.py` (same diagram as hand-laid eddx/SVG), `qwen38_flash_next_config.json`. Whole-model example (48-layer hybrid backbone, 70 boxes / 93 edges), archived and indexed per Step 8: `Qwen/Qwen3.8-Flash-Next/qwen38_flash_next_structure.{mmd,svg,png,eddx}` + preview HTML in the `/home/jiali/codes/model_structure` session workspace, listed in its root `index.html`, produced via `scripts/make_eddx_from_mermaid.py`.
