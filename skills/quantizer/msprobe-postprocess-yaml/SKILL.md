---
name: msprobe-postprocess-yaml
description: Generate msprobe tensor-postprocess YAML configs to invert msmodelslim quantization transforms (QuaRot rotation, fuse_ln, flex_smooth scaling) when comparing quantized-model dumps against float-model dumps. Use when the user has msmodelslim-quantized models (quarot.safetensors, quant_model_description.json), msprobe dump.json files from quantized vs float models, wants to reverse rotation/smoothing on activations before `msprobe compare`, or needs to build right_matmul matrix files (.npy) from safetensors rotations and checkpoint norm weights.
license: Apache-2.0
metadata:
  version: 0.2.1
  domain: quantization
  framework: msmodelslim
  protocol: cli
  skill_class: tool
  aliases:
    - msprobe-postprocess
    - postprocess-yaml
    - msprobe-compare-yaml
  trigger_intents:
    - 生成 msprobe 后处理配置
    - 反转量化变换后再比对
    - 生成 postprocess yaml
  keywords:
    - msprobe
    - tensor_postprocess
    - right_matmul
    - quarot
    - flex_smooth
    - fuse_ln
---

# msprobe Tensor Postprocess YAML Generation

## Overview

Generate a `right_matmul` tensor-postprocess YAML (and its `.npy` matrix files) so that
`msprobe compare --config <yaml>` first right-multiplies quantized-model dump tensors to undo
msmodelslim's quantization transforms (QuaRot rotation R, fuse_ln weight w, flex_smooth scale s),
making them directly comparable to float-model dumps.

Read [references/principles.md](references/principles.md) first — it explains the math
(why inverse rotation is `x @ R^T`, why LN outputs need composite `diag(s)@R^T@diag(w)`),
the msprobe YAML schema, data_name structure, per-position space classification, and the
mandatory validation checklist.

## Workflow

### Step 0: Collect inputs — ask the user for these

| Input | Typical path | Needed for |
|---|---|---|
| Rotation matrix | `<quant_model>/optional/quarot.safetensors` (key `global_rotation`) | all cases |
| Quant model description | `<quant_model>/quant_model_description.json` | confirm quarot/smooth usage |
| Float checkpoint dir | safetensors shards | LN composite matrices |
| Quant checkpoint dir | safetensors shards | smooth scale `s = 1/norm.weight` |
| Quantized dump.json | `<probe_dump>/step0/rank0/dump.json` | name coverage |
| Model forward code | e.g. `inference/model.py` or HF `modeling_*.py` | assigning dump tensors to spaces (Step 3.5) |
| msmodelslim source | repo root with `msmodelslim/model/<model_type>/model_adapter.py` | rotation points / Rb recreation |

The last two cannot be replaced by scripts: the forward code is the ground truth for which
activations sit in rotated space (rotation is baked into weights, so an activation's state is
decided by the weights it last passed through), and the model adapter's `get_rotate_map()`
lists exactly which linears got right/left rotation. If the user cannot provide them, fall
back to the embedded DeepSeek-style rules plus numerical forensics (e.g. `dsa_attn.input.1 ≡
input_layernorm.output` bit-exact ⇒ same space).

Determine which transforms ran (e.g. yaml config `quarot → flex_smooth_quant → linear_quant`).
If the user only wants the conservative rotation-only config (like the quarrot_only.yaml case),
skip Step 2.

### Step 1: Convert rotation to R^T .npy

```bash
python scripts/generate_postprocess.py convert-rot \
  --rot <quant_model>/optional/quarot.safetensors --outdir <matrices_dir>
```

Writes `Rt.npy` (transposed: msprobe right-multiplies, so inverting `x@R` requires `R^T`).
Never reference `.safetensors` directly — msprobe only loads `.npy`/`.pt` and fails silently.
For low-rank spaces (q_lora), Rb is never saved by msmodelslim (baked into wq_a/wq_b weights);
recreate it from the msmodelslim source with the same seed:

```bash
python scripts/generate_postprocess.py make-rb \
  --msmodelslim <msmodelslim_repo_or_install> --dim <q_lora_rank> --outdir <matrices_dir>
# defaults match deepseek-style runs: --rot-type BLOCK_HADAMARD_SHIFTED --block-size 32 --seed 1234
```

Confirm the seed/type against the user's msmodelslim version or the weight-level identity
(quant wq_a ≈ Rb^T @ (float wq_a · ln_w) ...).

### Step 2: Synthesize per-LN composite matrices

```bash
python scripts/generate_postprocess.py ln-matrices \
  --float-ckpt <float_dir> --quant-ckpt <quant_dir> \
  --rot <quant_model>/optional/quarot.safetensors \
  [--rb <rb.npy> --rb-norm-regexp 'q_norm'] \
  --outdir <matrices_dir>
```

Writes `model.layers.<i>.<norm>.npy` = `diag(1/quant_norm_w) @ R^T @ diag(float_norm_w)` and
`ln_matrices.json` (which norms carry smooth scale). Check its output: quant norm weight != 1
means flex_smooth was applied there.

### Step 3: Generate the YAML

```bash
python scripts/generate_postprocess.py generate \
  --dump-json <quant_dump>/dump.json --matrices-dir <matrices_dir> \
  --output <out>.yaml            # add --dry-run first to preview coverage
```

Embedded rules select candidates by module-path semantics only (class names wildcarded, arg
indices not hard-coded — they drift across framework/operator versions), then a **content
gate** uses dump.json's dtype/shape metadata to keep only tensors actually in the target
space (float dtype, last-dim == matrix dim, ndim >= 2 — this rejects int64 positions, 512-d
kv_norm, 1024-d q_lora tensors, etc.). Output schema is fixed to the verified working
structure: `right_matmul: {target_tensor_map: {matrix: [names]}, golden_tensor_map: {}}`
(target side only). Calibration baseline: on a real DeepSeek-family w8a8 dump the default
rules + gates reproduced a hand-verified reference config exactly.
Run `inspect` first on an unfamiliar model; adapt rules via `--rules`/`--extra-rule 'REGEX=MATRIX'`.

### Step 3.5: Verify space assignments with forward code + adapter (when provided)

Scripts only regex-match names; they cannot know a new model's data flow. When the user
supplied the forward code and msmodelslim adapter (Step 0), use them:

1. Read the adapter's `get_rotate_map()` — right-rotated (`W' = W @ R`) linears consume rotated
   inputs; left-rotated (`W' = R^T @ W`) linears write rotated outputs back to the residual stream.
2. Walk the forward code's data flow and assign every dump tensor: before any right-rotated
   weight ⇒ `x@R`; after a left-rotated write-back ⇒ rotated base; in low-rank mid-spaces ⇒ Rb;
   untouched branches (e.g. kv_norm) ⇒ no mapping.
3. Cross-check suspicious entries numerically (cosine of dump values against same-space
   candidates ≈ 1). Unknown provenance (warmup/MTP leftovers, cos ≈ 0.27 vs main path) ⇒ drop
   the mapping — a wrong matrix is worse than none.

### Step 4: Validate

```bash
python scripts/validate_yaml.py <out>.yaml --dump-json <quant_dump>/dump.json
```

Then apply the validation checklist in references/principles.md §6 — especially the
**direction check** (weight-level identity like `W_quant ≈ R^T @ W_float` on a purely
left-rotated layer, e.g. wo_b) since msprobe cannot detect a wrong-direction matrix, and an
end-to-end sample (restored tensor vs float dump, cos ≈ 1).

### Step 5: Compare

```bash
msprobe compare -tp <quant_dump>/dump.json -gp <float_dump>/dump.json \
    -o ./output --config <out>.yaml
```

The postprocess YAML is passed via `--config`.
(Real-data mode only: dump with task=tensor. Unmapped tensors pass through unchanged.)

## Script reference

Full CLI docs and gotchas: [references/script-usage.md](references/script-usage.md).
Scripts need `numpy`, `pyyaml`, `safetensors`; `make-rb` additionally needs an importable
msmodelslim (repo root or install dir).