"""
模块名称映射模块 - name_mapper.py

四级降级映射策略：
  Level 0: full_name 精确匹配
  Level 1：(op_type, module_path, layer_id, forward_number) 精确匹配
  Level 2: (op_type, apply_variants(module_path), layer_id, forward_number) 变体映射匹配
  Level 3: 边界内代码对比匹配

每层匹配完成后重新划定边界，下一层在边界区间内操作。
"""

import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from data_loader import OpData


def load_name_variant_map(config_path: str) -> List[Dict[str, str]]:
    """加载命名变体映射表"""
    if not os.path.exists(config_path):
        return []
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    return config.get("variants", [])


def apply_variant_mapping(name: str, variants: List[Dict[str, str]]) -> str:
    """应用命名变体映射表 (顺序敏感，长模式优先)"""
    for variant in variants:
        from_str = variant["from"]
        to_str = variant["to"]
        name = name.replace(from_str, to_str)
    return name


def get_level0_key(op:OpData) -> str:
    """Level 0 匹配 key: 完整算子名"""
    return op.full_name


def get_level1_key(op:OpData) -> Tuple:
    """
    Level 1 匹配 key: (op_type, module_path, layer_id, forward_number)
    """
    return (op.op_type, op.module_path, op.layer_id, op.forward_number)


def get_level2_key(op:OpData, variants: List[Dict[str, str]]) -> Tuple:
    """
    Level 2 匹配 key: (op_type, apply_variants(module_path), layer_id, forward_number)
    """
    mapped_path = apply_variant_mapping(op.module_path, variants)
    return (op.op_type, mapped_path, op.layer_id, op.forward_number)


class NameMapperResult:
    """映射结果"""

    def __init__(self):
        self.matched_pairs: List[Tuple[OpData, OpData, int]] = []
        self.unmatched_error: List[OpData] = []
        self.unmatched_normal: List[OpData] = []

        self.level0_count = 0
        self.level1_count = 0
        self.level2_count = 0
        self.level3_count = 0
        self.total_error = 0
        self.total_normal = 0

        self.boundaries: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []

    @property
    def coverage(self) -> float:
        total = len(self.matched_pairs)
        min_total = min(self.total_error, self.total_normal)
        if min_total == 0:
            return 1.0
        return total / min_total

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": {
                "total_error_operators": self.total_error,
                "total_normal_operators": self.total_normal,
                "matched_pairs": len(self.matched_pairs),
                "unmatched_error": len(self.unmatched_error),
                "unmatched_normal": len(self.unmatched_normal),
                "coverage": round(self.coverage, 4),
                "level0_matched": self.level0_count,
                "level1_matched": self.level1_count,
                "level2_matched": self.level2_count,
                "level3_matched": self.level3_count,
            },
            "matched_pairs": [
                {"error_name": e.full_name, "normal_name": n.full_name, "match_level": level}
                for e, n, level in self.matched_pairs
            ],
            "unmatched_error": [
                {"full_name": op.full_name, "op_type": op.op_type, "layer_id": op.layer_id}
                for op in self.unmatched_error
            ],
            "unmatched_normal": [
                {"full_name": op.full_name, "op_type": op.op_type, "layer_id": op.layer_id}
                for op in self.unmatched_normal
            ],
        }


def _build_boundaries_from_pairs(
    error_list: List[OpData],
    normal_list: List[OpData],
    matched_pairs: List[Tuple[OpData, OpData, int]],
) -> List[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """根据已匹配对划定独立区间"""
    error_idx_map = {op.full_name: i for i, op in enumerate(error_list)}
    normal_idx_map = {op.full_name: i for i, op in enumerate(normal_list)}

    match_points = []
    for e_op, n_op, _ in matched_pairs:
        e_idx = error_idx_map.get(e_op.full_name)
        n_idx = normal_idx_map.get(n_op.full_name)
        if e_idx is not None and n_idx is not None:
            match_points.append((e_idx, n_idx))

    match_points.sort(key=lambda x: x[0])

    if not match_points:
        return [((0, len(error_list)), (0, len(normal_list)))]

    boundaries = []

    first_e, first_n = match_points[0]
    if first_e > 0 or first_n > 0:
        boundaries.append(((0, first_e), (0, first_n)))

    for i in range(len(match_points) - 1):
        e_cur, n_cur = match_points[i]
        e_next, n_next = match_points[i + 1]
        e_start = e_cur + 1
        e_end = e_next
        n_start = n_cur + 1
        n_end = n_next
        if e_start < e_end or n_start < n_end:
            boundaries.append(((e_start, e_end), (n_start, n_end)))

    last_e, last_n = match_points[-1]
    if last_e < len(error_list) - 1 or last_n < len(normal_list) - 1:
        boundaries.append(((last_e + 1, len(error_list)), (last_n + 1, len(normal_list))))

    return boundaries


def _match_level_in_zone(
        zone_error: List[OpData],
        zone_normal: List[OpData],
        get_key_func,
        variants: List[Dict[str, str]] = None,
) -> List[Tuple[OpData, OpData]]:
    """
    在单个区间内执行基于 key 的匹配

    Args:
        zone_error: 区间内 error 算子
        zone_normal: 区间内 normal 算子
        get_key_func: 获取匹配 key 的函数，签名 (op) 或 (op, variants)
        variants: 命名变体映射表 （传给需要它的 get_key_func）

    Returns:
        匹配上的算子对列表
    """
    if not zone_error or not zone_normal:
        return []

    # 构建 normal 侧索引
    normal_index = defaultdict(list)
    used_normal: Set[str] = set()

    for op in zone_normal:
        if variants is not None:
            key = get_key_func(op, variants)
        else:
            key = get_key_func(op)
        normal_index[key].append(op)

    matched = []
    for e_op in zone_error:
        if variants is not None:
            e_key = get_key_func(e_op, variants)
        else:
            e_key = get_key_func(e_op)

        candidates = normal_index.get(e_key, [])
        matched_n = None
        for c in candidates:
            if c.full_name not in used_normal:
                matched_n = c
                break

        if matched_n is not None:
            matched.append((e_op, matched_n))
            used_normal.add(matched_n.full_name)

    return matched


class NameMapper:
    """模块名称映射器"""

    def __init__(self, config_dir: str):
        config_path = os.path.join(config_dir, "name_variant_map.json")
        self.variants = load_name_variant_map(config_path)

    def map(self, error_ops:Dict[str, OpData],
            normal_ops:Dict[str, OpData]) -> NameMapperResult:
        result = NameMapperResult()
        error_list = list(error_ops.values())
        normal_list = list(normal_ops.values())

        result.total_error = len(error_list)
        result.total_normal = len(normal_list)

        matched_error: Set[str] = set()
        matched_normal: Set[str] = set()

        # ====== Level 0: full_name 精确匹配 ======
        for e_op in error_list:
            n_op = normal_ops.get(e_op.full_name)
            if n_op is not None:
                result.matched_pairs.append((e_op, n_op, 0))
                matched_error.add(e_op.full_name)
                matched_normal.add(n_op.full_name)
                result.level0_count += 1

        # ====== Level 1: (op_type, module_path, layer_id, forward_number) 精确匹配 ======
        boundaries = _build_boundaries_from_pairs(error_list, normal_list, result.matched_pairs)

        for (e_start, e_end), (n_start, n_end) in boundaries:
            zone_error = [
                op for op in error_list[e_start:e_end]
                if op.full_name not in matched_error
            ]
            zone_normal = [
                op for op in normal_list[n_start:n_end]
                if op.full_name not in matched_normal
            ]

            new_matched = _match_level_in_zone(
                zone_error, zone_normal, get_level1_key
            )
            for e_op, n_op in new_matched:
                result.matched_pairs.append((e_op, n_op, 1))
                matched_error.add(e_op.full_name)
                matched_normal.add(n_op.full_name)
                result.level1_count += 1

        # ====== Level 2: (op_type, apply_variants(module_path), layer_id, forward_number) 变体匹配 ======
        boundaries = _build_boundaries_from_pairs(error_list, normal_list, result.matched_pairs)

        for (e_start, e_end), (n_start, n_end) in boundaries:
            zone_error = [
                op for op in error_list[e_start:e_end]
                if op.full_name not in matched_error
            ]
            zone_normal = [
                op for op in normal_list[n_start:n_end]
                if op.full_name not in matched_normal
            ]

            new_matched = _match_level_in_zone(
                zone_error, zone_normal, get_level2_key, self.variants
            )
            for e_op, n_op in new_matched:
                result.matched_pairs.append((e_op, n_op, 2))
                matched_error.add(e_op.full_name)
                matched_normal.add(n_op.full_name)
                result.level2_count += 1

        # 最终未匹配列表
        result.unmatched_error = [
            op for op in error_list if op.full_name not in matched_error
        ]
        result.unmatched_normal = [
            op for op in normal_list if op.full_name not in matched_normal
        ]
        
        # 最终边界（供 Level 3 使用）
        result.boundaries = _build_boundaries_from_pairs(
            error_list, normal_list, result.matched_pairs
        )

        return result
