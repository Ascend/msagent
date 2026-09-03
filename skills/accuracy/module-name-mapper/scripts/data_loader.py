"""
数据加载层 - data_loader.py

加载dump.json + construct.json + stack.json，解析算子各字段，构建 OpData。
"""

import json
import re
import os
from typing import Any, Dict, Optional, Tuple


LAYER_PATTERN = re.compile(r"\.layers\.(\d+)\.")
OP_TYPE_PREFIX = re.compile(
    r"^(Module|Tensor|NPU|Distributed|Functional|Torch)\."
)


class OpData:
    """算子的结构化数据"""

    def __init__(self, full_name: str):
        self.full_name: str = full_name
        # 从 full_name 解析的基础字段
        self.op_type: str = ""                 # Module / Tensor / NPU / ...
        self.module_path: str = ""             # 模块路径 (去掉前缀和类名)
        self.class_name: str = ""              # Python类名 (如 AscendRotaryEmbedding)
        self.instance_id: Optional[int] = None   # instance 编号 (仅 API 算子有)
        self.forward_number: Optional[int] = None   # forward.N 中的 N
        self.layer_id: Optional[int] = None    # Layer 编号
        self.call_direction: str = ""          # forward / backward
        self.core_name: str = ""               # 规范化后的核心名称 (用于匹配)

        # 来自 construct.json
        self.parent_name: Optional[str] = None  # 父节点名称

        # 来自 stack.json
        self.stack_entry: Optional[str] = None  # stack entry ID

        # 统计值 (来自 dump.json)
        self.stats: Dict[str, Any] = {}         # 输入输出统计值

        self._parse_full_name()

    def _parse_full_name(self):
        """从 full_name 解析各字段"""
        name = self.full_name
        parts = name.split(".")

        # 1. 提取 op_type
        type_match = OP_TYPE_PREFIX.match(name)
        if type_match:
            self.op_type = type_match.group(1)

        # 2. 提取 layer_id
        layer_match = LAYER_PATTERN.search(name)
        if layer_match:
            self.layer_id = int(layer_match.group(1))

        # 3. 提取 call_direction (forward/backward)
        # 检查算子末尾 （最后一个段） 是否为 forward 或 backward
        # Module： ...Attention.forward.0 → 末尾是 0，倒数第二段是 forward
        # API:    Tensor.reshape.1.forward → 末尾是 forward
        last_part = parts[-1] if parts else ""
        second_last = parts[-2] if len(parts) >=2 else ""
        if last_part in ("forward", "backward"):
            self.call_direction = last_part
        elif second_last in ("forward", "backward"):
            self.call_direction = second_last

        # 4. 提取 forward_number 和 instance_id
        # Module 算子: Module.model.layers.0.xxx.ClassName.forward.0
        #   → forward_number = 0 (forward 后面的数字)
        # API 算子:    Tensor.reshape.1.forward
        #   → instance_id = 1, forward_number = None (末尾 .forward 不带数字)
        # 先找末尾的 .forward.N 或 .backward.N (Module算子)
        fwd_match = re.search(r"\.(forward|backward)\.(\d+)$", name)
        if fwd_match:
            self.forward_number = int(fwd_match.group(2))
        else:
            # 没有末尾数字，可能是 API 算子的 .forward
            pass

        # 5. 找 instance_id (仅 API 算子有，Module 算子没有 instance_id)
        # API 算子末尾是 ".N.forward" 或 ".N.backward"
        # Tensor.__ge__.0.forward → 0 是 instance_id
        # Tensor.reshape.1.forward → 是 instance_id
        # Module 算子末尾是 ".ClassName.forward.N" → N 是 forward_number, 不是 instance_id
        if self.op_type != "Module":
            # API 算子: 检查倒数第二部分是否为数字
            # parts[-2] 是数字，parts[-1] 是 forward/backward
            if ((len(parts)) >= 2
                    and parts[-2].isdigit()
                    and parts[-1] in ("forward", "backward")):
                self.instance_id = int(parts[-2])

        # 6. 提取class_name (Module 算子中最后的类名)
        # Module.model.layers.0.xxx.AscendRotaryEmbedding.forward.0 → AscendRotaryEmbedding
        if self.op_type == "Module":
            # 去掉前缀后找类名
            stripped = name
            for prefix in ("Module.model.", "Module.module.module.", "Module.module."):
                if stripped.startswith(prefix):
                    stripped = stripped[len(prefix):]
                    break
            # 去掉 layers.N. 前缀
            stripped = re.sub(r"^layers\.\d+\.", "", stripped)
            # 在剩余部分中找 .ClassName.forward/backward
            class_match = re.search(r"\.([A-Z][a-zA-Z0-9]+)\.(?:forward|backward)", stripped)
            if class_match:
                self.class_name = class_match.group(1)

        # 7. 提取 module_path
        self._extract_module_path()

    def _extract_module_path(self):
        """提取模块路径 (用于人类阅读的简化路径)"""
        if self.op_type == "Module":
            # Module.model.layers.0.self_attn.rotary_emb.AscendRotaryEmbedding.forward.0
            # → 去掉框架前缀、去掉 layer.N.、去掉类名和后缀
            name = self.full_name
            for prefix in ("Module.model.", "Module.module.module.", "Module.module."):
                if name.startswith(prefix):
                    name = name[len(prefix):]
                    break
            # 去掉 decoder. 前缀
            name = re.sub(r"^decoder\.", "", name)
            # 提取 layers.N. 保留
            # 去掉最后的 ClassName.forward.N
            name = re.sub(r"\.[A-Z][a-zA-Z0-9]*\.(?:forward|backward)\.\d+$", "", name)
            self.module_path = name
        else:
            # API 算子: Tensor.reshape.1.forward → Tensor.reshape
            parts = self.full_name.split(".")
            if len(parts) >= 2:
                self.module_path = ".".join(parts[:2])

    def __repr__(self) -> str:
        return (
            f"OpData(full_name={self.full_name}, "
            f"op_type={self.op_type}, "
            f"layer_id={self.layer_id}, "
            f"core_name={self.core_name})"
        )


class DumpDataLoader:
    """加载 dump 数据"""

    def __init__(self, dump_dir: str):
        """
        Args:
            dump_dir: 包含 dump.json + construct.json + stack.json 的目录
        """
        self.dump_dir = dump_dir
        self.dump_path = os.path.join(dump_dir, "dump.json")
        self.construct_path = os.path.join(dump_dir, "construct.json")
        self.stack_path = os.path.join(dump_dir, "stack.json")

    def load_all(self) -> Tuple[Dict[str, OpData], Dict[str, Optional[str]], Dict[str, Any]]:
        """
        加载所有数据

        Returns:
            op_map: full_name → OpData
            construct_map: child_name → parent_name (或 None)
            stack_map: entry_id → stack_info
        """
        op_map = self._load_dump()
        construct_map = self._load_construct()
        stack_map = self._load_stack()

        # 将 construct 和 stack 信息关联到 OpData
        self._associate_construct(op_map, construct_map)
        self._associate_stack(op_map, stack_map)

        return op_map, construct_map, stack_map

    def _load_dump(self) -> Dict[str, OpData]:
        """加载 dump.json"""
        with open(self.dump_path, "r", encoding="utf-8") as f:
            dump_data = json.load(f)

        raw_data = dump_data.get("data", {})
        op_map: Dict[str, OpData] = {}

        for full_name, op_info in raw_data.items():
            op = OpData(full_name)
            op.stats = op_info
            op_map[full_name] = op

        return op_map

    def _load_construct(self) -> Dict[str, Optional[str]]:
        """加载 construct.json"""
        try:
            with open(self.construct_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def _load_stack(self) -> Dict[str, Any]:
        """加载 stack.json"""
        try:
            with open(self.stack_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def _associate_construct(self, op_map: Dict[str, OpData],
                             construct_map: Dict[str, Optional[str]]):
        """将 construct.json 的父子关系关联到 OpData"""
        for child_name, parent_name in construct_map.items():
            if child_name in op_map:
                op_map[child_name].parent_name = parent_name

    def _associate_stack(self, op_map: Dict[str, OpData],
                         stack_map: Dict[str, Any]):
        """将 stack.json 的调用信息关联到 OpData"""
        if not stack_map:
            return

        # stack.json 结构： entry_id → [op_list, stack_trace_list]
        for entry_id, entry_data in stack_map.items():
            if not isinstance(entry_data, list) or len(entry_data) < 1:
                continue
            op_list = entry_data[0]
            if isinstance(op_list, list):
                for op_name in op_list:
                    if op_name in op_map:
                        op_map[op_name].stack_entry = entry_id


def load_both_sides(error_dir: str, normal_dir:str
                    ) -> Tuple[Dict[str, OpData], Dict[str, OpData],
                               Dict[str, Optional[str]], Dict[str, Any], Dict[str, Any]]:
    """
    加载 error 侧和 normal 侧的 dump 数据

    Args:
        error_dir: error_dump 目录
        normal_dir: normal_dump 目录

    Returns:
        (error_op_map, normal_op_map, construct_map, error_stack_map, normal_stack_map)
    """
    error_loader = DumpDataLoader(error_dir)
    normal_loader = DumpDataLoader(normal_dir)

    error_op_map, error_construct, error_stack = error_loader.load_all()
    normal_op_map, normal_construct, normal_stack = normal_loader.load_all()

    # 使用 error 侧的 construct.json (两侧相同)
    construct_map = error_construct if error_construct else normal_construct

    return error_op_map, normal_op_map, construct_map, error_stack, normal_stack
