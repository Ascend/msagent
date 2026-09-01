"""
Generate msprobe cell_mapping.yaml from module_mapping_result.json.

对接 msprobe 的「不同平台不同配置下的模块对比」功能（cell_mapping）。
把 module-name-mapper 产出的 matched_pairs 转换为 msprobe `msprobe compare ... -cm cell_mapping.yaml`
所需的 cell 级映射文件。

粒度与匹配规则 100% 复刻 msprobe 的 process_cell_mapping (acc_compare.py):
  - cell_name = 去掉开头的 "Module."/"Cell." 前缀 + 切到 ".forward/.backward/.parameters_grad" 之前的整段
  （含 layer 索引， 如 model.layers.22.self_attn.wq_b.AscendColumnParallelLinear）
  - 采用「名称映射」格式（整段精确匹配），不做字符串子串映射：
    * 名称映射已包含完整路径+类名，一条即可精确覆盖（实测 733 对 100% 命中，零子串重叠）
    * 字符串子串映射有 replace(...,1) 只替换首处、且可能误伤包含该子串的其它 cell 的风险
  - 只处理 matched_pairs; absorbed(1:0) 等未匹配算子不计匹配，因此不进 cell_mapping

使用示例（在包含 .msagent 的目录下执行）:
  python .msagent/skills/module-name-mapper/scripts/generate_cell_mapping.py \
    --result results/module-mapping/module_mapping_result.json \
    --out results/module-mapping/cell_mapping.yaml

参数:
  --result   module_mapping_result.json 路径（必填）
  --out      输出 cell_mapping.yaml 路径（必填）

输出: 每行一条 `{error_cell}: {normal_cell}` 的 yaml 映射（仅含两侧 cell 名不同的映射对）。
"""

import argparse
import json
import os
import re
import yaml

# 与 msprobe const.REGEX_FORWARD_BACKWARD 一致；含 parameters_grad (acc_compare.py 也用其切分)
_FORWARD_BACKWARD_RE = re.compile(r"\.(?:forward|backward|parameters_grad)\.")
# 顶层前缀，process_cell_mapping 用 replace("Cell","Module") 后 split(".",1)[-1] 去掉
_TOP_PREFIX = "Module."


def extract_cell_name(op_name: str) -> str:
    """
    从完整算子名提取 cell_name（复刻 msprobe process_cell_mapping 的切分规则）。

    例：
      Module.model.embed_tokens.AscendVocabParallelEmbedding.forward.0
        -> model.embed_tokens.AscendVocabParallelEmbedding
      Module.model.layers.22.self_attn.wq_b.AscendColumnParallelLinear.forward.0.input.0
        -> model.layers.22.self_attn.wq_b.AscendColumnParallelLinear
    """
    op = op_name.replace("Cell", "Module", 1)
    # 去掉顶层前缀（"Module."），保留其后的模块路径+类名
    seg = op.split(".", 1)[-1] if op.startswith(_TOP_PREFIX) else op
    # 切到 .forward/.backward/.parameters_grad 之前，即 cell 名
    return _FORWARD_BACKWARD_RE.split(seg)[0]


def build_cell_mapping(result_path: str) -> dict:
    """从 module_mapping_result.json 的 matched_pairs 构建 error_cell → normal_cell 映射（去重）。"""
    with open(result_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    mapping = {}
    for pair in data.get("matched_pairs", []):
        error_name = pair.get("error_name")
        normal_name = pair.get("normal_name")
        if not error_name or not normal_name:
            continue
        error_cell = extract_cell_name(error_name)
        normal_cell = extract_cell_name(normal_name)
        # 两侧 cell 不同才需要映射；相同则无需写进 cell_mapping
        if error_cell != normal_cell:
            mapping[error_cell] = normal_cell
    return mapping


def write_cell_mapping(mapping: dict, out_path: str) -> None:
    """把映射写入 yaml 文件"""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        # sort_keys=False 保持 matched_pairs 原始顺序；allow_unicode 保证中文可读
        yaml.safe_dump(mapping, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def main():
    parser = argparse.ArgumentParser(
        description="Generate msprobe cell_mapping.yaml from module_mapping_result.json."
    )
    parser.add_argument("--result", required=True, help="module_mapping_result.json path")
    parser.add_argument("--out", required=True, help="output cell_mapping.yaml path")
    args = parser.parse_args()

    if not os.path.isfile(args.result):
        raise SystemExit(f"[cell_mapping] result file not found: {args.result}")

    mapping = build_cell_mapping(args.result)
    write_cell_mapping(mapping, args.out)

    print(f"[cell_mapping] loaded matched_pairs from: {args.result}")
    print(f"[cell_mapping] generated {len(mapping)} cell mappings -> {args.out}")


if __name__ == "__main__":
    main()

