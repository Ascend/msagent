#!/usr/bin/env python3
#!/usr/bin/env python3
"""Validate a generated msprobe tensor-postprocess YAML.

Checks (standalone, no msprobe import needed):
  1. YAML structure: right_matmul.target_tensor_map mapping is non-empty.
  2. Every referenced matrix file exists, loads, and is square.
  3. Every mapped data name exists in dump.json (when --dump-json given).
  4. Matrix dims are reported grouped, for manual sanity (no hard check).

If the installed msprobe provides TensorPostprocessManager, also loads the YAML through
it and reports any warnings (schema mismatch shows up here).
"""
import argparse
import json
import logging
import os
import sys

import numpy as np
import yaml


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("yaml_path")
    p.add_argument("--dump-json", help="quantized dump.json to verify name coverage")
    return p.parse_args()


def load_mapping(path):
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise SystemExit(f"[FAIL] yaml root is not a dict: {path}")
    rm = data.get("right_matmul")
    if not isinstance(rm, dict):
        raise SystemExit("[FAIL] missing 'right_matmul' top-level key")
    ttm = rm.get("target_tensor_map")
    if not isinstance(ttm, dict) or not ttm:
        raise SystemExit("[FAIL] missing 'right_matmul.target_tensor_map' mapping (or it is empty)")
    name_to_mat = {}
    for mat, names in ttm.items():
        if not isinstance(names, list):
            raise SystemExit(f"[FAIL] value under matrix {mat} is not a list")
        for n in names:
            if not isinstance(n, str):
                raise SystemExit(f"[FAIL] non-string entry under {mat}")
            name_to_mat[n] = mat
    return name_to_mat


def load_dump_names(path):
    def walk(obj, acc):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "data_name" and isinstance(v, str):
                    acc.add(v)
                else:
                    walk(v, acc)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, acc)

    with open(path) as f:
        data = json.load(f)
    names = set()
    if isinstance(data, dict):
        for k in data:
            if isinstance(k, str) and k.endswith(".pt"):
                names.add(k)
        walk(data, names)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                names.add(item)
            elif isinstance(item, dict):
                walk(item, names)
    return names


def main():
    args = parse_args()
    name_to_mat = load_mapping(args.yaml_path)
    print(f"[OK] schema parsed: {len(name_to_mat)} tensor entries")

    mats = sorted(set(name_to_mat.values()))
    bad = 0
    for m in mats:
        if not os.path.isfile(m):
            print(f"[FAIL] matrix file missing: {m}")
            bad += 1
            continue
        arr = np.load(m)
        if arr.ndim != 2 or arr.shape[0] != arr.shape[1]:
            print(f"[FAIL] matrix not square: {m} shape={arr.shape}")
            bad += 1
    dims = {}
    for m in mats:
        if os.path.isfile(m):
            dims.setdefault(np.load(m).shape[-1], []).append(m)
    print(f"[OK] {len(mats)} matrix files, dims: "
          f"{ {d: len(v) for d, v in dims.items()} }")
    if bad:
        sys.exit(1)

    if args.dump_json:
        dump_names = load_dump_names(args.dump_json)
        unknown = [n for n in name_to_mat if n not in dump_names]
        if unknown:
            print(f"[FAIL] {len(unknown)} mapped names not present in dump.json, e.g.:")
            for n in unknown[:10]:
                print(f"       {n}")
            sys.exit(1)
        print(f"[OK] all mapped names exist in {args.dump_json}")

    try:
        logging.basicConfig(level=logging.WARNING)
        from msprobe.core.compare.tensor_postprocess import TensorPostprocessManager  # noqa: F401
        mgr = TensorPostprocessManager(args.yaml_path)
        print("[OK] TensorPostprocessManager loaded the YAML without error")
    except ImportError:
        print("[INFO] installed msprobe has no tensor_postprocess module; "
              "schema must be verified on the compare machine")
    except Exception as e:
        print(f"[WARN] TensorPostprocessManager load issue: {e}")
    print("[DONE] validation finished")


if __name__ == "__main__":
    main()