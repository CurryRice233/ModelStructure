#!/usr/bin/env python3
"""Mechanical validation of qwen38_flash_next_structure.svg (no image viewing)."""
import html
import re
import sys
import itertools

SVG = "/home/jiali/codes/model_structure/Qwen/Qwen3.8-Flash-Next/qwen38_flash_next_structure.svg"
raw = open(SVG, encoding="utf-8").read()
svg = html.unescape(raw)

# ---------- 1. expected strings ----------
expected = [
    # box titles (first lines)
    "input_ids", "pixel_values (图像/视频, 可选)", "embed_tokens (Embedding)",
    "patch_embed (Conv3d)", "+ pos_embed (Embedding + bilinear 插值)",
    "norm1 (LayerNorm)", "VisionAttention", "+ residual", "norm2 (LayerNorm)",
    "VisionMLP", "PatchMerger: LayerNorm [1152] + reshape 2×2",
    "masked_scatter: 图像/视频嵌入替换占位符 token 嵌入",
    "repeat × hc_count(4) → hyper-connection 4 流",
    "rotary_emb (MRoPE, interleaved)",
    "create masks",
    "shift + XOR 哈希 (ngram_size 3)",
    "ngram_embedding (Embedding) + flatten",
    "key_proj (Linear) + norm_key (RMSNorm)",
    "value_proj (Linear)",
    "norm_query (RMSNorm)",
    "gate = sign(g)·√|k·q/√2560| → σ(gate) × value",
    "norm_conv (RMSNorm) + 膨胀 depthwise Conv1d + SiLU",
    "+ PLE 注入 (仅 layer 1)",
    "hc_norm (RMSNorm)",
    "input_mix_weight_down (Linear) ÷4 → SiLU",
    "input_mix_weight_up (Linear) → sigmoid",
    "权重展开 [B, S, 4, 2560] × 归一化流, mean 于 4 流",
    "block_inject_weight (Linear) ÷4 → 2·sigmoid",
    "× conv_mask (padding 置零)",
    "in_proj_qkv (Linear)",
    "causal Conv1d (depthwise) + SiLU",
    "split + reshape",
    "in_proj_z (Linear)",
    "in_proj_b (Linear) → sigmoid = β",
    "in_proj_a (Linear) + softplus",
    "repeat_interleave q,k ×3 (nv/nk = 48/16)",
    "gated_delta_rule (chunk 64): l2norm(q,k), q/√128",
    "RMSNormGated (norm × σ(z))",
    "out_proj (Linear)",
    "index_qk_proj (Linear)",
    "q_layernorm (RMSNorm) + RoPE(rot 64)",
    "update_indexer: key 缓存拼接",
    "按块均值池化 (R(4) token/块)",
    "k_layernorm (RMSNorm) + RoPE(块首位置)",
    "scores = relu(q_i·kᵀ) 对头求和 /√128 → topk(512)",
    "块索引 → token 索引 + 尾部不足一块的 token",
    "scatter 成布尔掩码 (−1 吸收到末位后丢弃)",
    "attention_mask = causal + indexer 掩码",
    "q_proj (Linear) → chunk(q, gate)",
    "k_proj (Linear)",
    "v_proj (Linear)",
    "q_norm + k_norm (RMSNorm)",
    "partial MRoPE (rot 64 / 256)",
    "update KV cache",
    "稀疏注意力 (indexer 掩码后, sdpa/eager)",
    "× σ(gate) → o_proj (Linear)",
    "hyper-combine: hyper_input + attn_out ⊗ inject 权重",
    "mlp_hyper_connection (GatedResidual, 结构同 attn_hyper_connection)",
    "TopKRouter: Linear + softmax + topk(10)",
    "gate_up_proj → SiLU(gate)·up → down_proj",
    "× topk 权重, index_add 累加",
    "shared_expert MLP",
    "shared_expert_gate (Linear) → sigmoid",
    "expert_out + σ(gate)·shared_out",
    "hyper-combine: hyper_input + mlp_out ⊗ inject 权重",
    "hyper_connection_mixer (GatedResidual, use_combine=False)",
    "lm_head (Linear)",
    "logits",
    # concrete config-derived values
    "[V(248320), H(2560)]",
    "= 10240, H(2560)]",
    "[vd(6144), H(2560)]",
    "[nv(48), H(2560)]",
    "[H(2560), vd(6144)]",
    "[dv(128)], eps(1e-6), act(sigmoid)",
    "[(nq(4)+nkv(1))·Id(128) = 640, H(2560)]",
    "[Id(128)], eps(1e-6)",
    "budget(2048)/R(4) = 512",
    "[B, S, 2051]",
    "[nq(24)·Id(256)·2 = 12288, H(2560)]",
    "[nkv(2)·Id(256) = 512, H(2560)]",
    "[Id(256)], eps(1e-6)",
    "[B, nkv(2), S_full, 256]",
    "GQA groups(12)",
    "[H(2560), 6144]",
    "[hc(10240)], group(2560), eps(1e-6)",
    "[lr(320), hc(10240)]",
    "[hc(10240), lr(320)]",
    "[hc(4), hc(10240)]",
    "W [E(512), H(2560)]",
    "gate_up [2·Im(640) = 1280, H(2560)]",
    "down [H(2560), Im(640)]",
    "[1, H(2560)], bias(False)",
    "rot = Id(256)×0.25 = 64, theta(1e7), sections [11, 11, 10]",
    "[≈3.2e8 (16 个 ≈2e7 素数词表, 补齐 128 倍数), 160] (~95GiB)",
    "[1152, 3, 2, 16, 16]",
    "[2304, 1152]",
    "[4304, 1152]",
    "[4608, 4608]",
    "[2560, 4608]",
    "kd(2048)·2 + vd(6144) = 10240",
    "recurrent state [B, nv(48), dk(128), dv(128)]",
    "state_len (k−1)·d = 9",
    "[B, S, 248320]",
]
missing = [s for s in expected if s not in svg]
print(f"[1] strings: {len(expected) - len(missing)}/{len(expected)} present")
for m in missing:
    print("    MISSING:", m)

# ---------- 2. color counts ----------
matmul_rects = len(re.findall(r'<rect[^>]*fill:#90d6ab', svg)) + len(re.findall(r'<rect[^>]*fill="rgb\(144,\s*214,\s*171\)"', svg))
act_rects = len(re.findall(r'<rect[^>]*fill:#83ebdc', svg)) + len(re.findall(r'<rect[^>]*fill="rgb\(131,\s*235,\s*220\)"', svg))
# mermaid may use classes instead of inline fills
if matmul_rects == 0:
    matmul_rects = len(re.findall(r'class="[^"]*\bmatmul\b[^"]*"', svg))
if act_rects == 0:
    act_rects = len(re.findall(r'class="[^"]*\bact\b[^"]*"', svg))
print(f"[2] fills: matmul(green)={matmul_rects} expected=28 | act(blue)={act_rects} expected=10")

# ---------- 3. node geometry / overlap ----------
node_re = re.compile(
    r'<g[^>]*class="node[^"]*"[^>]*id="(?:my-svg-)?(?:flowchart-)?([A-Za-z_][A-Za-z0-9_]*)-\d+"[^>]*transform="translate\(([-\d.]+),\s*([-\d.]+)\)"[^>]*>(.*?)(?=<g[^>]*class="node|</svg>)',
    re.S,
)
rect_re = re.compile(r'<rect[^>]*x="([-\d.]+)"[^>]*y="([-\d.]+)"[^>]*width="([-\d.]+)"[^>]*height="([-\d.]+)"')
boxes = {}
for m in node_re.finditer(raw):
    nid, tx, ty, body = m.group(1), float(m.group(2)), float(m.group(3)), m.group(4)
    r = rect_re.search(body)
    if not r:
        continue
    x, y, w, h = (float(v) for v in r.groups())
    boxes[nid] = (tx + x - w / 2 if abs(x) < 1e-9 else tx + x, ty + y, w, h)
# mermaid centers rects at (0,0): x=-w/2, y=-h/2
boxes2 = {}
for m in node_re.finditer(raw):
    nid, tx, ty, body = m.group(1), float(m.group(2)), float(m.group(3)), m.group(4)
    r = rect_re.search(body)
    if not r:
        continue
    x, y, w, h = (float(v) for v in r.groups())
    boxes2[nid] = (tx + x, ty + y, w, h)

def overlap(a, b, tol=1.0):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx + tol or bx + bw <= ax + tol or ay + ah <= by + tol or by + bh <= ay + tol)

print(f"[3] parsed {len(boxes2)} node boxes")
overlaps = []
for (n1, b1), (n2, b2) in itertools.combinations(boxes2.items(), 2):
    if overlap(b1, b2):
        overlaps.append((n1, n2))
print(f"[3] overlapping pairs: {len(overlaps)}")
for o in overlaps[:20]:
    print("    OVERLAP:", o)

ok = not missing and matmul_rects == 28 and act_rects == 10 and not overlaps and len(boxes2) >= 60
print("RESULT:", "PASS" if ok else "CHECK")
sys.exit(0 if ok else 1)
