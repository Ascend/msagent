#!/usr/bin/env python3
"""Generate msprobe tensor-postprocess YAML configs for msmodelslim-quantized model dumps.

Subcommands:
  convert-rot   Convert a rotation matrix file (safetensors/npz/npy/pt) to transposed .npy
                (msprobe right_matmul does `tensor @ mat`; to invert x@R you need R^T).
  ln-matrices   Synthesize per-LayerNorm composite matrices from float/quant checkpoints:
                M = diag(1/quant_norm_w) @ R^T @ diag(float_norm_w)
  generate      Scan dump.json data names, classify them with regex rules, emit YAML.
  inspect       Print dump data names and rule-matching preview (no YAML written).

Only .npy/.pt matrix files can be referenced by msprobe YAML; .safetensors silently
fails inside msprobe's _try_matmul (tensor passes through unchanged, one warning log).
"""
import argparse
import glob
import json
import os
import re
import sys

import numpy as np
import yaml

SEP = "."


def err(msg):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def info(msg):
    print(f"[INFO] {msg}")


def sanitize(name):
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def load_matrix(path, key=None):
    ext = os.path.splitext(path)[1].lower()
    if not os.path.isfile(path):
        err(f"matrix file not found: {path}")
    if ext == ".npy":
        return np.load(path)
    if ext == ".pt":
        import torch
        obj = torch.load(path, map_location="cpu")
        if hasattr(obj, "numpy"):
            return obj.numpy()
        return np.asarray(obj)
    if ext == ".safetensors":
        from safetensors import safe_open
        try:
            with safe_open(path, framework="np") as f:
                keys = list(f.keys())
                return _pick(f.get_tensor, keys, key, path)
        except Exception:
            with safe_open(path, framework="pt") as f:
                keys = list(f.keys())
                return _pick(lambda k: f.get_tensor(k).numpy(), keys, key, path)
    if ext == ".npz":
        d = np.load(path)
        return _pick(lambda k: d[k], d.files, key, path)
    err(f"unsupported matrix format: {ext} (accepted: .npy/.pt/.safetensors/.npz)")


def _pick(getter, keys, key, path):
    if key is not None:
        if key not in keys:
            err(f"key '{key}' not in {path}; available: {keys}")
        return np.asarray(getter(key))
    if len(keys) == 1:
        return np.asarray(getter(keys[0]))
    err(f"{path} contains multiple tensors {keys}; pass --key")


def as_square(mat, src):
    mat = np.asarray(mat, dtype=np.float32)
    if mat.ndim != 2 or mat.shape[0] != mat.shape[1]:
        err(f"matrix from {src} is not square: shape={mat.shape}")
    return mat


def orthogonality_report(name, mat):
    gram = mat @ mat.T
    diag = np.sqrt(np.diag(gram))
    off = gram - np.diag(diag)
    off_norm = float(np.abs(off).max()) if off.size else 0.0
    sym = bool(np.allclose(mat, mat.T, atol=1e-3))
    info(f"{name}: shape={mat.shape}, max|R@R^T - diag|={off_norm:.2e}, "
         f"diag range=[{diag.min():.4f}, {diag.max():.4f}], symmetric={sym}")
    if diag.min() < 0.9 or diag.max() > 1.1 or off_norm > 1e-2:
        print(f"[WARN] {name} does not look orthogonal; using it as inverse rotation is suspect",
              file=sys.stderr)


# ---------------------------------------------------------------- convert-rot

def cmd_convert_rot(args):
    mat = as_square(load_matrix(args.rot, args.key), args.rot)
    out = mat.T if not args.no_transpose else mat
    os.makedirs(args.outdir, exist_ok=True)
    out_name = args.name
    out_path = os.path.join(args.outdir, out_name + ".npy")
    np.save(out_path, out.astype(np.float32))
    info(f"saved {out_path} ({'R^T (transposed)' if not args.no_transpose else 'as-is'})")
    orthogonality_report(out_name, out)
    if not args.no_transpose:
        info(f"transposed={not np.allclose(mat, mat.T)} "
             f"(if False, R was symmetric and transpose is a no-op)")


# ------------------------------------------------------------------- make-rb

def _load_quarot_utils(path):
    try:
        import msmodelslim.processor.quarot.common.quarot_utils as qu
        return qu
    except Exception:
        pass
    if not path:
        err("msmodelslim not importable; pass --msmodelslim <repo root | site-packages dir | quarot_utils.py path>")
    roots = []
    if path.endswith(".py"):
        d = os.path.dirname(os.path.abspath(path))
        while d and os.path.basename(d) != "msmodelslim":
            d = os.path.dirname(d)
        if d:
            roots.append(os.path.dirname(d))
    else:
        roots.append(path)
        roots.append(os.path.dirname(path))  # site-packages style dir containing msmodelslim/
    for root in roots:
        pkg = os.path.join(root, "msmodelslim")
        if not os.path.isdir(pkg):
            continue
        sys.path.insert(0, root)
        try:
            import msmodelslim.processor.quarot.common.quarot_utils as qu
            return qu
        except Exception:
            pass
        finally:
            sys.path.pop(0)
    err(f"could not import quarot_utils from {path}; check that it contains the msmodelslim package")


def _find_rot_enum_value(mod, value_name):
    import enum as _enum
    for obj in vars(mod).values():
        if isinstance(obj, type) and issubclass(obj, _enum.Enum):
            member = getattr(obj, value_name, None)
            if member is not None:
                return member
    return value_name  # fall back to raw string


def cmd_make_rb(args):
    import inspect
    qu = _load_quarot_utils(args.msmodelslim)
    create_rot = getattr(qu, "create_rot", None)
    if create_rot is None:
        err("create_rot not found in quarot_utils")
    sig = inspect.signature(create_rot)
    kwargs = {}
    for name, p in sig.parameters.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        nl = name.lower()
        if "block" in nl:
            kwargs[name] = args.block_size
        elif any(k in nl for k in ("type", "mode", "method")):
            kwargs[name] = _find_rot_enum_value(qu, args.rot_type)
        elif any(k in nl for k in ("dim", "size", "rank", "hidden")):
            kwargs[name] = args.dim
        elif nl == "seed":
            kwargs[name] = args.seed
        elif p.default is inspect.Parameter.empty and not any(
                k in nl for k in ("device", "dtype", "path", "name")):
            err(f"cannot map required create_rot parameter '{name}' (signature: {sig}); "
                f"write a custom snippet calling create_rot instead")
    info(f"calling create_rot({kwargs})")
    mat = create_rot(**kwargs)
    if hasattr(mat, "detach"):
        mat = mat.detach().cpu().numpy()
    mat = as_square(np.asarray(mat), "create_rot output")
    os.makedirs(args.outdir, exist_ok=True)
    out_path = os.path.join(args.outdir, "Rb_t.npy")
    np.save(out_path, mat.T.astype(np.float32))
    info(f"saved {out_path} (Rb^T, transposed; msprobe right-multiplies)")
    orthogonality_report("Rb_t", mat.T)
    print("[NOTE] Rb is never saved by msmodelslim (baked into wq_a/wq_b weights). "
          "After generating, verify against weights, e.g. quant wq_a ~ Rb^T @ (float wq_a @ ln_w) ... "
          "see references/principles.md validation checklist.", file=sys.stderr)


# --------------------------------------------------------------- ln-matrices

def iter_shard_files(path):
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "*.safetensors")))
        if not files:
            err(f"no .safetensors shards under {path}")
        return files
    return [path]


def load_norm_weights(ckpt, norm_regex):
    weights = {}
    rx = re.compile(norm_regex)
    for shard in iter_shard_files(ckpt):
        from safetensors import safe_open
        with safe_open(shard, framework="np") as f:
            for k in f.keys():
                if rx.search(k):
                    t = f.get_tensor(k)
                    if t.ndim == 1:
                        weights[k] = np.asarray(t, dtype=np.float32)
    return weights


def cmd_ln_matrices(args):
    rt_path = os.path.join(args.outdir, "Rt.npy")
    rt = as_square(load_matrix(rt_path if os.path.exists(rt_path) else args.rot, args.key), "Rt")
    rbt = None
    if args.rb:
        rbt = as_square(load_matrix(args.rb, args.rb_key), "Rb").T  # store Rb^T

    float_w = load_norm_weights(args.float_ckpt, args.norm_regexp)
    quant_w = load_norm_weights(args.quant_ckpt, args.norm_regexp)
    common = sorted(set(float_w) & set(quant_w))
    if not common:
        err("no common norm keys between float/quant checkpoints; check --norm-regexp")

    rb_rx = re.compile(args.rb_norm_regexp) if args.rb_norm_regexp else None
    os.makedirs(args.outdir, exist_ok=True)
    summary = {}
    for k in common:
        fw, qw = float_w[k], quant_w[k]
        if fw.shape != qw.shape:
            info(f"skip {k}: shape mismatch {fw.shape} vs {qw.shape}")
            continue
        use_rb = rb_rx is not None and rb_rx.search(k) is not None
        base = rbt if use_rb else rt
        if fw.shape[0] != base.shape[0]:
            info(f"skip {k}: dim {fw.shape[0]} != rotation dim {base.shape[0]}"
                 f"{' (pass --rb/--rb-norm-regexp for low-rank spaces)' if not use_rb else ''}")
            continue
        if np.any(np.abs(qw) < 1e-6):
            print(f"[WARN] {k}: near-zero quant norm weight; 1/s unstable", file=sys.stderr)
        s = 1.0 / qw
        m = (s[:, None] * base) * fw[None, :]
        name = sanitize(k)
        np.save(os.path.join(args.outdir, name + ".npy"), m.astype(np.float32))
        # rules look up by norm key stem (e.g. model.layers.0.input_layernorm)
        if name.endswith(".weight"):
            np.save(os.path.join(args.outdir, name[:-len(".weight")] + ".npy"),
                    m.astype(np.float32))
        had_smooth = not np.allclose(qw, 1.0, atol=1e-3)
        summary[name] = {"norm_key": k, "dim": int(fw.shape[0]), "smooth": bool(had_smooth),
                         "space": "rb" if use_rb else "rt"}
    smooth_cnt = sum(v["smooth"] for v in summary.values())
    info(f"wrote {len(summary)} LN composite matrices to {args.outdir}")
    info(f"norms with smooth scale (quant weight != 1): {smooth_cnt}/{len(summary)}; "
         f"smooth means quant norm weight = 1/s (s = flex_smooth scale)")
    with open(os.path.join(args.outdir, "ln_matrices.json"), "w") as f:
        json.dump(summary, f, indent=2)

# ------------------------------------------------------------------ generate

DEFAULT_RULES = [
    # Design: patterns only select candidates by module-path semantics (class names
    # wildcarded with \w+ / [\w.]+ — they vary across framework/operator versions);
    # the real space discrimination is done by content gates in classify():
    # float dtype + last-dim == matrix dim + ndim >= 2. That kills version-dependent
    # traps like input.0 being positions (int64), 1-D norm weights, or low-rank
    # (q_lora 1024 / kv 512) tensors without hard-coding arg indices.
    #
    # --- residual stream (pure rotation) ---
    {"pattern": r"^Module\.model\.embed_tokens\.\w+\.forward\.\d+\.output\.\d+\.pt$",
     "matrix": "Rt", "desc": "embedding output (residual stream start)"},
    {"pattern": r"^Module\.model\.norm\.\w+\.forward\.\d+\.input\.0\.pt$",
     "matrix": "Rt", "desc": "final norm input (residual stream)"},
    # DecoderLayer io: all arg indices; int64 positions/mask args are dropped by the
    # dtype gate, hidden/residual (float, ..., hidden) pass
    {"pattern": r"^Module\.model\.layers\.\d+\.\w+\.forward\.\d+\.(?:input|output)\.\d+\.pt$",
     "matrix": "Rt", "desc": "decoder layer io (hidden/residual)"},
    {"pattern": r"^Module\.model\.layers\.\d+\.(?:input_layernorm|post_attention_layernorm)\.\w+\.forward\.\d+\.input\.0\.pt$",
     "matrix": "Rt", "desc": "layernorm input (residual stream)"},
    # attention / MoE block outputs: broad path match; non-residual tensors
    # (wkv/kv_norm 512-d, wq_a 1024-d, wq_b heads*d, gate 256-d ...) are dropped by
    # the dim gate
    {"pattern": r"^Module\.model\.layers\.\d+\.self_attn\.[\w.]+\.forward\.\d+\.output\.\d+\.pt$",
     "matrix": "Rt", "desc": "attention-family output (residual stream)"},
    {"pattern": r"^Module\.model\.layers\.\d+\.mlp\.[\w.]+\.forward\.\d+\.output\.\d+\.pt$",
     "matrix": "Rt", "desc": "mlp/MoE-family output (residual stream)"},
    # --- composite LN-space rules (auto-skipped when matrix files are absent) ---
    {"pattern": r"^Module\.model\.layers\.(?P<layer>\d+)\.input_layernorm\.\w+\.forward\.\d+\.output\.\d+\.pt$",
     "matrix": "model.layers.{layer}.input_layernorm", "desc": "input_layernorm output"},
    {"pattern": r"^Module\.model\.layers\.(?P<layer>\d+)\.post_attention_layernorm\.\w+\.forward\.\d+\.output\.\d+\.pt$",
     "matrix": "model.layers.{layer}.post_attention_layernorm", "desc": "post_attention_layernorm output"},
    {"pattern": r"^Module\.model\.layers\.(?P<layer>\d+)\.self_attn\.dsa_attn\.\w+\.forward\.\d+\.input\.1\.pt$",
     "matrix": "model.layers.{layer}.input_layernorm", "desc": "dsa_attn input.1 (LN output space)"},
    {"pattern": r"^Module\.model\.layers\.(?P<layer>\d+)\.mlp\.\w+\.forward\.\d+\.input\.0\.pt$",
     "matrix": "model.layers.{layer}.post_attention_layernorm", "desc": "MoE/FFN input (LN output space)"},
    {"pattern": r"^Module\.model\.norm\.\w+\.forward\.\d+\.output\.\d+\.pt$",
     "matrix": "model.norm", "desc": "final norm output"},
    # --- low-rank q_lora space (requires --rb matrix named Rb_t) ---
    {"pattern": r"^Module\.model\.layers\.(?P<layer>\d+)\.self_attn\.q_norm\.\w+\.forward\.\d+\.(input|output)\.\d+\.pt$",
     "matrix": "Rb_t", "desc": "q_norm input/output (q_lora rotation space)"},
]


def load_rules(path, extra_rules):
    rules = list(DEFAULT_RULES)
    if path:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        rules = data.get("rules", rules)
    for spec in extra_rules or []:
        if "=" not in spec:
            err(f"--extra-rule must be 'regex=matrix', got: {spec}")
        pat, mat = spec.split("=", 1)
        rules.append({"pattern": pat, "matrix": mat})
    return rules


def _walk_dump_entries(obj, acc):
    """Recursively collect {data_name: {dtype, shape}} from a real msprobe dump.json
    (names live under data/<module>/{input_args,input_kwargs,output}/.../data_name,
    each next to dtype/shape metadata)."""
    if isinstance(obj, dict):
        if isinstance(obj.get("data_name"), str):
            meta = {}
            if isinstance(obj.get("dtype"), str):
                meta["dtype"] = obj["dtype"]
            if isinstance(obj.get("shape"), list):
                meta["shape"] = obj["shape"]
            acc[obj["data_name"]] = meta
        for v in obj.values():
            _walk_dump_entries(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _walk_dump_entries(v, acc)


def load_dump_entries(path):
    """Return {data_name: {dtype, shape}} — names plus metadata for content gates.
    Metadata may be absent (plain-text name lists); gates then degrade to no-ops."""
    if not os.path.isfile(path):
        err(f"dump file not found: {path}")
    with open(path) as f:
        first = f.read(1)
    if not path.endswith(".json") and first not in "[{":
        return {l.strip(): {} for l in open(path) if l.strip()}
    data = json.load(open(path))
    entries = {}
    if isinstance(data, dict):
        for k in data:
            if isinstance(k, str) and k.endswith(".pt"):
                entries[k] = {}
        _walk_dump_entries(data, entries)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                entries[item] = {}
            elif isinstance(item, dict):
                _walk_dump_entries(item, entries)
    if not entries:
        err(f"could not extract any data names from {path}; "
            f"inspect its structure and pass a plain text file of names instead")
    return entries


_MATRIX_DIM_CACHE = {}


def matrix_dim(path):
    if path not in _MATRIX_DIM_CACHE:
        arr = np.load(path, mmap_mode="r")
        _MATRIX_DIM_CACHE[path] = int(arr.shape[-1])
    return _MATRIX_DIM_CACHE[path]


def content_gate(name, mat_path, entries):
    """Version-agnostic space discrimination: a tensor that a rule routes to `mat_path`
    must be floating-point and have last-dim == matrix dim and ndim >= 2.
    Returns (ok, reason). Absent metadata => ok (gate is best-effort)."""
    meta = entries.get(name)
    if not meta:
        return True, ""
    dtype = meta.get("dtype", "")
    if dtype and "float" not in dtype and "bfloat" not in dtype:
        return False, f"dtype={dtype}"
    shape = meta.get("shape")
    if not shape:
        return True, ""
    if len(shape) < 2:
        return False, f"ndim={len(shape)}"
    last = shape[-1]
    if last != matrix_dim(mat_path):
        return False, f"last-dim={last} != matrix-dim {matrix_dim(mat_path)}"
    return True, ""


def resolve_matrix_path(mat, matrices_dir):
    if os.path.isabs(mat):
        return mat if os.path.exists(mat) else None
    p = os.path.join(matrices_dir, mat if mat.endswith(".npy") else mat + ".npy")
    return p if os.path.exists(p) else None


def load_dump_names(path):
    return sorted(load_dump_entries(path))


def classify(names, rules, matrices_dir, entries=None):
    mapping = {}
    skipped = []
    filtered = {}
    for rule in rules:
        rx = re.compile(rule["pattern"])
        for n in names:
            if n in mapping:
                continue
            m = rx.fullmatch(n)
            if not m:
                continue
            mat = rule["matrix"]
            for gname, gval in (m.groupdict() or {}).items():
                if gval is not None:
                    mat = mat.replace("{" + gname + "}", gval)
            for i in range(1, m.re.groups + 1):
                gv = m.group(i)
                if gv is not None:
                    mat = mat.replace("{%d}" % i, gv)
            p = resolve_matrix_path(mat, matrices_dir)
            if p is None:
                skipped.append((n, mat, rule.get("desc", "")))
                continue
            ok, reason = content_gate(n, p, entries or {})
            if not ok:
                filtered.setdefault(reason, []).append(n)
                continue
            mapping[n] = os.path.realpath(p)

    return mapping, skipped, filtered


def emit_yaml(mapping, out_path):
    by_matrix = {}
    for name, mat in sorted(mapping.items(), key=lambda kv: (kv[1], kv[0])):
        by_matrix.setdefault(mat, []).append(name)
    content = {"right_matmul": {"target_tensor_map": dict(sorted(by_matrix.items())),
                                 "golden_tensor_map": {}}}
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        yaml.safe_dump(content, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    info(f"wrote {out_path}: {len(mapping)} tensor entries, {len(by_matrix)} matrices")
    return len(mapping), len(by_matrix)


def report(names, mapping, skipped, filtered=None):
    matched = set(mapping)
    unmatched = [n for n in names if n not in matched]
    info(f"coverage: {len(matched)}/{len(names)} data names mapped")
    if skipped:
        print(f"[WARN] {len(skipped)} names matched a rule whose matrix file is missing "
              f"(left unmapped):", file=sys.stderr)
        seen = set()
        for n, mat, desc in skipped:
            if mat not in seen:
                seen.add(mat)
                print(f"       missing matrix: {mat}  ({desc})", file=sys.stderr)
    if filtered:
        total = sum(len(v) for v in filtered.values())
        print(f"[INFO] {total} rule-matched names rejected by content gate "
              f"(dtype/dim not in target space):", file=sys.stderr)
        for reason, ns in sorted(filtered.items()):
            print(f"       {reason}: {len(ns)} e.g. {ns[0]}", file=sys.stderr)
    if unmatched:
        print(f"[INFO] {len(unmatched)} unmapped names pass through unchanged, e.g.:", file=sys.stderr)
        for n in unmatched[:15]:
            print(f"       {n}", file=sys.stderr)
    return unmatched


def cmd_generate(args):
    entries = load_dump_entries(args.dump_json)
    names = sorted(entries)
    rules = load_rules(args.rules, args.extra_rule)
    mapping, skipped, filtered = classify(names, rules, args.matrices_dir, entries)
    report(names, mapping, skipped, filtered)
    if args.dry_run:
        info("dry-run: no YAML written")
        return
    emit_yaml(mapping, args.output)


def cmd_inspect(args):
    entries = load_dump_entries(args.dump_json)
    names = sorted(entries)
    info(f"{len(names)} data names in {args.dump_json}")
    paths = {}
    for n in names:
        m = re.match(r"^Module\.(?P<path>.+)\.\w+\.(?:forward|backward)\.\d+\.(?P<io>input|output)\.\d+\.pt$", n)
        if m:
            paths.setdefault(m.group("path"), set()).add(m.group("io"))
    print("\nmodule paths (count, io kinds, dtype/shape variety):")
    for p in sorted(paths):
        pn = [n for n in names if n.startswith("Module." + p + ".")]
        metas = {(entries[n].get("dtype"), tuple(entries[n].get("shape", [])))
                 for n in pn if entries[n]}
        print(f"  {p}  x{len(pn)}  {sorted(paths[p])}")
        for d, s in sorted(metas, key=lambda x: str(x))[:4]:
            print(f"      {d} {s}")
    if args.rules:
        rules = load_rules(args.rules, args.extra_rule)
        mapping, skipped, filtered = classify(names, rules, args.matrices_dir, entries)
        report(names, mapping, skipped, filtered)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("convert-rot", help="convert rotation file to transposed .npy")
    p.add_argument("--rot", required=True, help="rotation matrix file (safetensors/npz/npy/pt)")
    p.add_argument("--key", help="tensor key inside the file (default: sole key)")
    p.add_argument("--outdir", required=True)
    p.add_argument("--name", default="Rt", help="output matrix name (default Rt)")
    p.add_argument("--no-transpose", action="store_true",
                   help="keep matrix as-is (only if it is already the inverse)")
    p.set_defaults(func=cmd_convert_rot)

    p = sub.add_parser("ln-matrices", help="synthesize per-LN composite matrices")
    p.add_argument("--float-ckpt", required=True, help="float model checkpoint dir (safetensors shards)")
    p.add_argument("--quant-ckpt", required=True, help="quantized model checkpoint dir")
    p.add_argument("--rot", help="rotation file (used if OUTDIR/Rt.npy absent)")
    p.add_argument("--key", help="tensor key inside rotation file")
    p.add_argument("--rb", help="Rb matrix file for low-rank spaces (e.g. q_lora rotation)")
    p.add_argument("--rb-key", help="tensor key inside Rb file")
    p.add_argument("--rb-norm-regexp", help="norm keys that live in Rb space (e.g. q_norm)")
    p.add_argument("--norm-regexp", default=r"norm\.weight$",
                   help="key regexp for norm weights (default 'norm\\.weight$')")
    p.add_argument("--outdir", required=True)
    p.set_defaults(func=cmd_ln_matrices)

    p = sub.add_parser("make-rb", help="recreate the never-saved q_lora rotation Rb^T "
                                       "via msmodelslim create_rot (same seed reproduces it)")
    p.add_argument("--msmodelslim", required=True,
                   help="msmodelslim repo root / site-packages dir / quarot_utils.py path")
    p.add_argument("--dim", type=int, required=True,
                   help="rotation dim (e.g. q_lora_rank=1024)")
    p.add_argument("--rot-type", default="BLOCK_HADAMARD_SHIFTED",
                   help="create_rot type/mode value (default BLOCK_HADAMARD_SHIFTED)")
    p.add_argument("--block-size", type=int, default=32)
    p.add_argument("--seed", type=int, default=1234,
                   help="must match the value msmodelslim used (default 1234)")
    p.add_argument("--outdir", required=True)
    p.set_defaults(func=cmd_make_rb)

    p = sub.add_parser("generate", help="scan dump.json and emit postprocess YAML")
    p.add_argument("--dump-json", required=True, help="quantized model dump.json (or text file of names)")
    p.add_argument("--matrices-dir", required=True)
    p.add_argument("--rules", help="custom rules yaml (defaults embedded; see references)")
    p.add_argument("--extra-rule", action="append",
                   help="additional rule 'regex=matrix' (repeatable)")
    p.add_argument("--output", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("inspect", help="list dump data names and classification preview")
    p.add_argument("--dump-json", required=True)
    p.add_argument("--matrices-dir", default=".")
    p.add_argument("--rules")
    p.add_argument("--extra-rule", action="append")
    p.set_defaults(func=cmd_inspect)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()