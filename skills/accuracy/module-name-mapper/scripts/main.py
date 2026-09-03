"""
Module Name Mapper - 模块名称映射专用入口

包含 Level 1+2 的自动映射和 Level 3 的代码对比模糊匹配。

使用示例（包含 .msagent 的目录下执行）：
  # 仅 Level 1+2
  python .msagent/skills/module-name-mapper/scripts/main.py \
    --error-dump test-data/0707a/error_dump \
    --normal-dump test-data/0707a/normal_dump \
    --out-dir results/module-mapping

  #完整流程（含 Level 3 代码对比）
  python .msagent/skills/module-name-mapper/scripts/main.py \
    --error-dump test-data/0728/error_npu_dump \
    --normal-dump test-data/0728/normal_gpu_dump \
    --code-root /path/to/code-repo/vllm-ascend,/path/to/code-repo/vllm \
    --out-dir results/module-mapping

  # 关闭 Level 3
  python .msagent/skills/module-name-mapper/scripts/main.py \
    --error-dump test-data/0707a/error_dump \
    --normal-dump test-data/0707a/normal_dump \
    --disable-level3 \
    --out-dir results/module-mapping
"""

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# 确保脚本所在目录在包搜索路径中
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from data_loader import load_both_sides
from name_mapper import NameMapper, NameMapperResult
from code_comparator import CodeComparator, ZoneCompareResult


def build_report(result: NameMapperResult, error_dir: str,
                 normal_dir: str, out_dir: str,
                 zone_results: Optional[List[ZoneCompareResult]] = None) -> str:
    """生成 Markdown 映射报告"""
    lines = [
        f"# Module Name Mapper 报告",
        f"",
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**error 侧**: {error_dir}",
        f"**normal 侧**: {normal_dir}",
        f"**输出目录**: {os.path.abspath(out_dir)}",
        f"",
        f"---",
        f"",
        f"## 一、映射统计",
        f"",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| error 算子总数 | {result.total_error} |",
        f"| normal 算子总数 | {result.total_normal} |",
        f"| 匹配对数 | {len(result.matched_pairs)} |",
        f"| 匹配覆盖率 | {result.coverage:.2%} |",
        f"| 未匹配 error 算子 | {len(result.unmatched_error)} |",
        f"| 未匹配 normal 算子 | {len(result.unmatched_normal)} |",
        f"",
        f"### 各级匹配统计",
        f"",
        f"| 匹配级别 | 数量 | 占比 |",
        f"|---------|------|------|",
        f"| Level 0 (full_name 精确匹配) | {result.level0_count} | {_pct(result.level0_count, len(result.matched_pairs))} |",
        f"| Level 1 (模块路径精确匹配) | {result.level1_count} | {_pct(result.level1_count, len(result.matched_pairs))} |",
        f"| Level 2 (变体映射匹配) | {result.level2_count} | {_pct(result.level2_count, len(result.matched_pairs))} |",
        f"| Level 3 (代码对比) | {result.level3_count} | {_pct(result.level3_count, len(result.matched_pairs))} |",
        f"",
    ]

    # Level 3 代码对比详情
    if zone_results:
        has_match = False
        for zone in zone_results:
            if zone.processed and zone.matched_pairs:
                if not has_match:
                    lines.extend([
                        f"---",
                        f"",
                        f"## 二、Level 3 代码对比结果",
                        f"",
                    ])
                    has_match = True

                lines.extend([
                    f"### 区间 {zone.zone_index}",
                    f"",
                    f"| error 算子 | normal 算子 | 置信度 | 判断理由 |",
                    f"|-----------|-------------|--------|----------|",
                ])
                for pair in zone.matched_pairs:
                    lines.append(
                        f"| `{pair.error_op.full_name}` | "
                        f"`{pair.normal_op.full_name}` | "
                        f"{pair.confidence} | {pair.reason} |"
                    )
                lines.append("")

                if zone.error_unmatched:
                    lines.append(f"**区间 {zone.zone_index} error 侧独有**:")
                    for op in zone.error_unmatched:
                        lines.append(f"- `{op.full_name}`")
                    lines.append("")

                if zone.normal_unmatched:
                    lines.append(f"**区间 {zone.zone_index} normal 侧独有**:")
                    for op in zone.normal_unmatched:
                        lines.append(f"- `{op.full_name}`")
                    lines.append("")

    if result.unmatched_error:
        lines.extend([
            f"---",
            f"",
            f"## 三、error 侧独有算子 ({len(result.unmatched_error)} 个)",
            f"",
            f"| 模块名称 | 类型 | Layer |",
            f"|---------|------|-------|",
        ])
        for op in result.unmatched_error[:50]:
            lines.append(
                f"| `{op.full_name}` | {op.op_type} | "
                f"{op.layer_id if op.layer_id is not None else '-'} | "
            )
        if len(result.unmatched_error) > 50:
            lines.append(f"| ... 共 {len(result.unmatched_error)} 个，仅展示前 50 个 ... | | |")
        lines.append("")

    if result.unmatched_normal:
        lines.extend([
            f"---",
            f"",
            f"## 四、normal 侧独有算子 ({len(result.unmatched_normal)} 个)",
            f"",
            f"| 模块名称 | 类型 | Layer |",
            f"|---------|------|-------|",
        ])
        for op in result.unmatched_normal[:50]:
            lines.append(
                f"| `{op.full_name}` | {op.op_type} | "
                f"{op.layer_id if op.layer_id is not None else '-'} | "
            )
        if len(result.unmatched_normal) > 50:
            lines.append(f"| ... 共 {len(result.unmatched_normal)} 个，仅展示前 50 个 ... | | |")
        lines.append("")

    lines.extend([
        f"---",
        f"",
        f"## 五、诊断建议",
        f"",
    ])
    if result.coverage >= 0.95:
        lines.append(f"- **映射完整度高 ({result.coverage:.0%})**：两侧模块名称高度一致。")
    elif result.coverage >= 0.8:
        lines.append(f"- **映射完整度良好 ({result.coverage:.0%})**：大部分算子已对齐。")
    else:
        lines.append(f"- **映射完整度偏低 ({result.coverage:.0%})**：两侧算子命名存在较大差异。")
        lines.append(f"  1. 检查 `name_variant_map.json` 中的映射表是否完整")
        lines.append(f"  2. 考虑是否两侧使用了不同的模型架构或者框架版本")
    if result.level3_count > 0:
        lines.append(
            f"- **Level 3 代码对比完成 {result.level3_count} 对映射**：建议将确认正确的映射规则 "
            f"添加到 `name_variant_map.json` 中，下次运行时即可升级为 Level 2 规范匹配。"
        )
    lines.append("")
    return "\n".join(lines)


def _pct(count: int, total: int) -> str:
    if total == 0:
        return "-"
    return f"{count / total:.1%}"


def prompt_level3_usage() -> Tuple[bool, List[str]]:
    """
    交互式询问 Level 3 配置

    Returns:
         (enable_level3, code_roots)
    """
    print("\n" + "=" * 60)
    print("  Level 3 代码对比匹配")
    print("=" * 60)
    print("  Level 3 通过对比算子源代码来判断两侧功能是否等价，")
    print("  可以帮助对齐因命名差异或框架升级导致名称不同的算子。")
    print("  此功能需要提供代码仓路径")
    print()

    response = input("  是否关闭 Level 3？(y/N): ").strip().lower()
    if response == "y" or response == "yes":
        print("  Level 3 已关闭，仅执行 Level 1+2 映射。")
        return False, []

    print()
    print("  请输入代码仓根目录路径（多个路径用逗号分隔）:")
    print("  例如: D:/repo/vllm-ascend,D:/repo/vllm")
    code_root_input = input("  code-root: ").strip()

    code_roots = []
    if code_root_input:
        code_roots = [
            p.strip().rstrip("/\\")
            for p in code_root_input.split(",")
            if p.strip()
        ]

    if not code_roots:
        print("  [警告] 未提供代码仓路径， Level 3 将无法检索代码，建议稍后通过 --code-root 参数提供。")

    return True, code_roots


def main():
    parser = argparse.ArgumentParser(
        description="Module Name Mapper - 模块名称映射"
    )
    parser.add_argument(
        "--error-dump", required=True,
        help="error 侧 dump 目录（包含 dump.json + construct.json + stack.json）"
    )
    parser.add_argument(
        "--normal-dump", required=True,
        help="normal 侧 dump 目录"
    )
    parser.add_argument(
        "--out-dir", default="results/module-mapping",
        help="输出目录（默认: results/module-mapping）"
    )
    parser.add_argument(
        "--config-dir", default=None,
        help="配置目录"
    )
    parser.add_argument(
        "--code-root", default=None,
        help="代码仓根目录路径（多个用逗号隔开），开启 Level 3 代码对比时需要提供"
    )
    parser.add_argument(
        "--disable-level3", action="store_true",
        help="关闭 Level 3 代码对比匹配"
    )
    parser.add_argument(
        "--non-interactive", action="store_true",
        help="非交互模式，不提示 Level 3 相关问题"
    )

    args = parser.parse_args()

    # 确定配置目录
    if args.config_dir is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_dir = os.path.join(os.path.dirname(script_dir), "config")
    else:
        config_dir = args.config_dir

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    error_dir = os.path.abspath(args.error_dump)
    normal_dir = os.path.abspath(args.normal_dump)

    # Level 3 配置
    enable_level3 = not args.disable_level3
    code_roots: List[str] = []

    if args.code_root:
        code_roots = [p.strip().rstrip("/\\") for p in args.code_root.split(",") if p.strip()]

    if enable_level3 and not args.non_interactive and not sys.stdin.isatty():
        # 非终端模式，根据参数决定
        pass

    print(f"[Module Name Mapper] 加载数据...")
    print(f"  error:{error_dir}")
    print(f"  normal:{normal_dir}")

    error_ops, normal_ops, construct_map, error_stack, normal_stack = load_both_sides(
        error_dir, normal_dir
    )
    # 本 skill 只做 Module 级模块名称映射
    # 在此把两侧算子过滤为 Module.*，使 Level 0-3 映射、覆盖率、报告、variant 建议
    # 全部只针对 Module 算子
    error_ops = {k: v for k, v in error_ops.items() if k.startswith("Module.")}
    normal_ops = {k: v for k, v in normal_ops.items() if k.startswith("Module.")}
    error_list = list(error_ops.values())
    normal_list = list(normal_ops.values())

    print(f"  error Module 算子: {len(error_ops)}")
    print(f"  normal Module 算子: {len(normal_ops)}")

    print(f"\n[Module Name Mapper] 执行模块名称映射...")
    mapper = NameMapper(config_dir)

    # 交互式询问 Level 3 配置
    if enable_level3 and (not code_roots) and (not args.non_interactive):
        enable_level3, code_roots = prompt_level3_usage()

    if not code_roots and enable_level3:
        # 尝试从 args.code_root 再读取一次
        if args.code_root:
            code_roots = [p.strip().rstrip("/\\") for p in args.code_root.split(",") if p.strip()]

    result = mapper.map(error_ops, normal_ops)

    print(f"\n  匹配对数: {len(result.matched_pairs)}")
    print(f"  覆盖率: {result.coverage:.2%}")
    print(f"    Level 0: {result.level0_count}")
    print(f"    Level 1: {result.level1_count}")
    print(f"    Level 2: {result.level2_count}")
    print(f"    Level 3 (当前): {result.level3_count}")
    print(f"  未匹配 error: {len(result.unmatched_error)}")
    print(f"  未匹配 normal: {len(result.unmatched_normal)}")

    # Level 3 代码对比
    zone_results = None
    if enable_level3 and code_roots and result.boundaries:
        # 检查是否有需要的代码对比的区间
        has_unmatched = False
        for (e_start, e_end), (n_start, n_end) in result.boundaries:
            zone_error = [op for op in error_list[e_start:e_end]
                          if op.full_name not in {e.full_name for e, _, _ in result.matched_pairs}]
            zone_normal = [op for op in normal_list[n_start:n_end]
                           if op.full_name not in {n.full_name for _, n, _ in result.matched_pairs}]
            if zone_error and zone_normal:
                has_unmatched = True
                break

        if has_unmatched:
            print(f"\n[Module Name Mapper] 执行 Level 3 代码对比匹配...")
            print(f"  代码仓: {code_roots}")

            comparator = CodeComparator(config_dir, code_roots,
                                        error_stack, normal_stack)

            zones_dir = os.path.join(out_dir, "level3-zones")
            zone_results = comparator.compare_zones(
                result, error_list, normal_list, zones_dir
            )

            # 检查是否有未处理的 prompt
            total_prompts = 0
            for zr in (zone_results or []):
                zone_file = os.path.join(zones_dir, f"zone_{zr.zone_index}.json")
                if os.path.exists(zone_file):
                    with open(zone_file, "r", encoding="utf-8") as f:
                        zd = json.load(f)
                    total_prompts += len(zd.get("prompt_pairs", []))

            if total_prompts > 0:
                print(f"\n  [Level 3] 生成了 {total_prompts} 对算子的代码比 prompt")
                print(f"  [Level 3] prompt 文件保存在: {zones_dir}")
                print(f"  [Level 3] 请查阅 zone_*_prompts.txt 文件中每对算子的详细对比请求")
                print(f"  [Level 3] 或查阅 zone_*_summary.txt 获取区间概述")
            else:
                print(f"\n  [Level 3] 无需要代码对比的未匹配算子")
                print(f"\n[Module Name Mapper] 无需 Level 3：所有区间已全覆盖。")
    elif enable_level3 and not code_roots:
        print(f"\n[Module Name Mapper] 跳过 Level 3：未提供代码仓路径（使用 --code-root 指定）。")
    elif not enable_level3:
        print(f"\n[Module Name Mapper] Level 3 已关闭。")

    # 输出结果
    json_path = os.path.join(out_dir, "module_mapping_result.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
    print(f"\n[Module Name Mapper] 输出 JSON: {json_path}")

    report = build_report(result, error_dir, normal_dir, out_dir, zone_results)
    report_path = os.path.join(out_dir, "module_mapping_result.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[Module Name Mapper] 输出报告: {report_path}")

    # 如果有 Level 3 结果且未全部处理，生成建议添加的映射规则
    if zone_results:
        suggestions = generate_mapping_suggestions(zone_results)
        if suggestions:
            suggest_path = os.path.join(out_dir, "suggested_variant_additions.json")
            with open(suggest_path, "w", encoding="utf-8") as f:
                json.dump(suggestions, f, ensure_ascii=False, indent=2)
            print(f"[Module Name Mapper] 映射建议: {suggest_path}")

    print(f"\n[Module Name Mapper] 完成！")


def generate_mapping_suggestions(
        zone_results: List[ZoneCompareResult]
) -> Optional[Dict]:
    """根据 Level 3 结果生成映射规则建议"""
    suggestions = []
    for zone in zone_results:
        if not zone.processed:
            continue
        for pair in zone.matched_pairs:
            if pair.confidence in ("high", "medium"):
                # 从算子名中提取可能的映射片段
                e_name = pair.error_op.full_name
                n_name = pair.normal_op.full_name
                # 推测可能的规则
                suggestions.append({
                    "from": "...",
                    "to": "...",
                    "confidence": pair.confidence,
                    "source": "code_compare",
                    "reason": pair.reason,
                    "example_error": e_name,
                    "example_normal": n_name,
                })

    if not suggestions:
        return None

    return {
        "_description": "AI 建议的映射规则，请审阅后选择性添加到 name_variant_map.json",
        "suggested_additions": suggestions,
    }


if __name__ == "__main__":
    main()
