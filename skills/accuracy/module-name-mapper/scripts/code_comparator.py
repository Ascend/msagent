"""
代码对比模块 - code_comparator.py

实现 Level 3 代码对比匹配
负责按区间打包未匹配算子，检索代码，构造 prompt，调用大模型进行代码对比。
解析结果并更新映射
"""

import json
import os
import re
import sys
from typing import Dict, List, Optional

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from data_loader import OpData
from name_mapper import  NameMapperResult
from code_retriever import CodeRetriever


def _is_matched(resp: Dict) -> bool:
    """
    解析大模型响应，判断该算子对是否建立算子级映射

    兼容两种响应格式：
    - 旧格式 {matched: bool}
    - 新格式 {relation: 'matched'|'absorbed'|'not_matched'}

    absorbed(1:0, kernel 吸收) 与 not_matched 均视为**不建立算子级映射**。
    默认（缺省时）视为matched。
    """
    if "matched" in resp:
        return bool(resp.get("matched", True))
    relation = resp.get("relation", "matched")
    return relation == "matched"


class CodeCompareResult:
    """单个算子对的代码对比结果"""

    def __init__(self, error_op: OpData, normal_op:OpData,
                 matched: bool, confidence: str, reason: str):
        self.error_op = error_op
        self.normal_op = normal_op
        self.matched = matched
        self.confidence = confidence
        self.reason = reason


class ZoneCompareResult:
    """单个区间的代码对比结果"""

    def __init__(self, zone_index: int,
                 error_ops: List[OpData], normal_ops: List[OpData]):
        self.zone_index = zone_index
        self.error_ops = error_ops
        self.normal_ops = normal_ops
        self.matched_pairs: List[CodeCompareResult] = []
        self.error_unmatched: List[OpData] = []
        self.normal_unmatched: List[OpData] = []
        self.processed = False

    def to_dict(self) -> Dict:
        return {
            "zone_index": self.zone_index,
            "error_ops": [op.full_name for op in self.error_ops],
            "normal_ops": [op.full_name for op in self.normal_ops],
            "processed": self.processed,
            "matched_pairs": [
                {
                    "error_name": r.error_op.full_name,
                    "normal_name": r.normal_op.full_name,
                    "confidence": r.confidence,
                    "reason": r.reason,
                }
                for r in self.matched_pairs
            ],
            "error_unmatched": [op.full_name for op in self.error_unmatched],
            "normal_unmatched": [op.full_name for op in self.normal_unmatched],
        }


class CodeComparator:
    """
    代码对比器

    按区间处理未匹配算子，检索代码后构造 prompt，调用大模型进行代码对比。
    """

    def __init__(self, config_dir:str, code_roots: List[str],
                 error_stack_map: Dict, normal_stack_map: Dict):
        self.config_dir = config_dir
        self.code_roots = code_roots
        self.error_stack_map = error_stack_map
        self.normal_stack_map = normal_stack_map
        self.retriever = CodeRetriever(code_roots)
        self.prompt_template = self._load_prompt_template()

    def _load_prompt_template(self) -> str:
        """加载 prompt 模板"""
        prompt_path = os.path.join(
            os.path.dirname(self.config_dir), "assets", "code_compare_prompt.md"
        )
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def compare_zones(self, result: NameMapperResult,
                      error_list: List[OpData],
                      normal_list: List[OpData],
                      zones_dir: str) -> List[ZoneCompareResult]:
        """
        对所有边界区间执行代码对比

        Args:
            result: 当前映射结果（包含已匹配对和边界信息）
            error_list: 原始顺序的 error 算子列表
            normal_list: 原始顺序的 normal 算子列表
            zones_dir: 中间结果保存目录

        Returns:
            每个区间的对比结果
        """
        os.makedirs(zones_dir, exist_ok=True)
        zone_results = []

        # 构造已匹配集合
        matched_error = {e.full_name for e, _, _ in result.matched_pairs}
        matched_normal = {n.full_name for _, n, _ in result.matched_pairs}

        used_error: set = set(matched_error)
        used_normal: set = set(matched_normal)

        for zi, ((e_start, e_end), (n_start, n_end)) in enumerate(result.boundaries):
            # 获取区间内的未匹配算子
            zone_error = [
                op for op in error_list[e_start:e_end]
                if op.full_name not in used_error
            ]
            zone_normal = [
                op for op in normal_list[n_start:n_end]
                if op.full_name not in used_normal
            ]

            # 过滤：一侧为空则跳过
            if not zone_error or not zone_normal:
                for op in zone_error:
                    used_error.add(op.full_name)
                for op in zone_normal:
                    used_normal.add(op.full_name)
                continue

            print(f"\n  [Level 3] 区间 {zi}: "
                  f"error {len(zone_error)} 个, "
                  f"normal {len(zone_normal)} 个")

            zone_result = ZoneCompareResult(zi, zone_error, zone_normal)

            # 检索代码
            error_codes = self.retriever.batch_retrieve(
                zone_error, self.error_stack_map
            )
            normal_codes = self.retriever.batch_retrieve(
                zone_normal, self.normal_stack_map
            )

            # 生产 prompt 文件（供大模型阅读和判断）
            pairs = []
            for e_op in zone_error:
                for n_op in zone_normal:
                    prompt_text = self._build_compare_prompt(
                        e_op, n_op,
                        error_codes.get(e_op.full_name),
                        normal_codes.get(n_op.full_name)
                    )
                    if prompt_text:
                        pairs.append({
                            "error_name": e_op.full_name,
                            "normal_name": n_op.full_name,
                            "prompt": prompt_text,
                        })

            # 保存中间结果和 prompt
            zone_file = os.path.join(zones_dir, f"zone_{zi}.json")
            # batch_retrieve 对检索失败的算子返回 None（存在值为 None）。
            # 此处把 None 归一为 {}，避免 .get("source_code") 在 None 上崩溃
            def _safe(ops, codes):
                return [
                    {"name": op.full_name,
                     "code": (codes.get(op.full_name) or {}).get("source_code", ""),
                     "class_name": (codes.get(op.full_name) or {}).get("class_name", ""),
                     "source_file": (codes.get(op.full_name) or {}).get("source_file", "")}
                    for op in ops
                ]
            zone_data = {
                "zone_index": zi,
                "error_ops": _safe(zone_error, error_codes),
                "normal_ops": _safe(zone_normal, normal_codes),
                "prompt_pairs": [
                    {"error_name": p["error_name"], "normal_name": p["normal_name"]}
                    for p in pairs
                ],
                "processed": False,
            }
            with open(zone_file, "w", encoding="utf-8") as f:
                json.dump(zone_data, f, ensure_ascii=False, indent=2)

            # 将 prompt 保存到一个独立的文件中，方便大模型直接读取
            if pairs:
                prompt_file = os.path.join(zones_dir, f"zone_{zi}_prompts.txt")
                with open(prompt_file, "w", encoding="utf-8") as f:
                    for i, p in enumerate(pairs):
                        f.write(f"{'='*60}\n")
                        f.write(f"配对 {i + 1}/{len(pairs)}\n")
                        f.write(f"error: {p['error_name']}\n")
                        f.write(f"normal: {p['normal_name']}\n")
                        f.write(f"{'='*60}\n\n")
                        f.write(p["prompt"])
                        f.write("\n\n")

                # 也生成一个精简版，每个配对只保留关键信息
                summary_file = os.path.join(zones_dir, f"zone_{zi}_summary.txt")
                with open(summary_file, "w", encoding="utf-8") as f:
                    f.write(f"区间 {zi} 未匹配算子 - 代码对比请求\n")
                    f.write(f"{'='*60}\n\n")
                    f.write(f"error 侧 ({len(zone_error)} 个):\n")
                    for op in zone_error:
                        code_info = error_codes.get(op.full_name) or {}
                        f.write(f"  {op.full_name}\n")
                        f.write(f"    类名: {code_info.get('class_name', 'N/A')}\n")
                        f.write(f"    文件: {code_info.get('source_file', 'N/A')}\n")
                    f.write(f"\nnormal 侧 ({len(zone_normal)} 个):\n")
                    for op in zone_normal:
                        code_info = normal_codes.get(op.full_name) or {}
                        f.write(f"  {op.full_name}\n")
                        f.write(f"    类名: {code_info.get('class_name', 'N/A')}\n")
                        f.write(f"    文件: {code_info.get('source_file', 'N/A')}\n")
                    f.write(f"\n{'='*60}\n")
                    f.write("请对每对 (error, normal) 算子判断功能是否等价。\n")
                    f.write("详细 prompt 在 zone_{zi}_prompts.txt 中。\n")

            zone_results.append(zone_result)

        return zone_results

    def _build_compare_prompt(self, error_op: OpData, normal_op: OpData,
                              error_code: Optional[Dict],
                              normal_code: Optional[Dict]) -> Optional[str]:
        """
        构造一对算子的代码对比 prompt

        Args:
            error_op: error 侧算子
            normal_op: normal 侧算子
            error_code: error 侧代码检索结果
            normal_code: normal 侧代码检索结果

        Returns:
            填充好的 prompt 文本，如果代码不可用则返回 None
        """
        if not error_code or not normal_code:
            return None

        if not self.prompt_template:
            return None

        prompt = self.prompt_template
        prompt = prompt.replace("{{error_name}}", error_op.full_name)
        prompt = prompt.replace("{{error_class_name}}",
                                error_code.get("class_name", ""))
        prompt = prompt.replace("{{error_code}}",
                                error_code.get("source_code", ""))
        prompt = prompt.replace("{{normal_name}}", normal_op.full_name)
        prompt = prompt.replace("{{normal_class_name}}",
                                normal_code.get("class_name", ""))
        prompt = prompt.replace("{{normal_code}}",
                                normal_code.get("source_code", ""))
        return prompt

    def parse_llm_response(self, response: str) -> Optional[Dict]:
        """解析大模型返回的 JSON 结果"""
        try:
            # 尝试从响应中提取 JSON
            json_match = re.search(r'\{[^}]+\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return  json.loads(response)
        except (json.JSONDecodeError, Exception):
            return None

    def compute_llm_matches(self, zone_results: List[ZoneCompareResult],
                            llm_responses: Dict[str, Dict]) -> List[ZoneCompareResult]:
        """
        应用大模型的响应结果更新区间对比结果

        Args:
            zone_results: 区间对比结果列表
            llm_responses: 大模型响应字典
                          {(error_name, normal_name): {matched, confidence, reason}}

        Returns:
            更新后的区间对比结果
        """
        for zone in zone_results:
            if not zone.processed:
                continue

            new_matched = []
            new_error_unmatched = list(zone.error_unmatched) if zone.error_unmatched else []
            new_normal_unmatched = list(zone.normal_unmatched) if zone.normal_unmatched else []

            for pair in zone.matched_pairs:
                key = (pair.error_op.full_name, pair.normal_op.full_name)
                if key in llm_responses:
                    resp = llm_responses[key]
                    pair.matched = _is_matched(resp)
                    pair.confidence = resp.get("confidence", "low")
                    pair.reason = resp.get("reason", "")
                    if pair.matched:
                        new_matched.append(pair)
                    else:
                        # not_matched 或 absorbed 均视为不建立算子级映射
                        new_error_unmatched.append(pair.error_op)
                        new_normal_unmatched.append(pair.normal_op)

            zone.matched_pairs = new_matched
            zone.error_unmatched = new_error_unmatched
            zone.normal_unmatched = new_normal_unmatched

        return zone_results

    @staticmethod
    def update_mapping_result(result: NameMapperResult,
                              zone_results: List[ZoneCompareResult],
                              error_list: List[OpData],
                              normal_list: List[OpData]):
        """
        将代码比对结果更新到 NameMapperResult 中

        更新内容：
          - 添加新的 matched_pairs (match_level=3)
          - 更新未匹配算子列表
          - 更新已匹配对的位置信息（用于后续区间重算）
        """
        used_error = {e.full_name for e, _, _ in result.matched_pairs}
        used_normal = {n.full_name for _, n, _ in result.matched_pairs}

        for zone in zone_results:
            if not zone.processed:
                continue

            for pair in zone.matched_pairs:
                if pair.error_op.full_name not in used_error:
                    result.matched_pairs.append((pair.error_op, pair.normal_op, 3))
                    result.level3_count += 1
                    used_error.add(pair.error_op.full_name)
                    used_normal.add(pair.normal_op.full_name)

        # 更新未匹配列表
        result.unmatched_error = [
            op for op in error_list if op.full_name not in used_error
        ]
        result.unmatched_normal = [
            op for op in normal_list if op.full_name not in used_normal
        ]
