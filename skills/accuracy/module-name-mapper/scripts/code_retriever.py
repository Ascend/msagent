"""
代码检索模块 - code_retriever.py

负责从 stack.json 路径和类名搜索两种方式获取算子源代码。
支持容器路径和本地代码仓映射两种模式。
"""

import os
import re
import sys
from typing import  Dict, List, Optional, Tuple

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from data_loader import  OpData


class CodeRetriever:
    """代码检索器"""

    def __init__(self, code_roots: List[str] = None):
        """
        Args:
            code_roots: 代码仓根目录列表。每个元素是一个代码仓的根目录。
                        例如：["/workspace/vllm-ascend", "/workspace/vllm"]
        """
        self.code_roots = code_roots or []

    def set_code_roots(self, code_roots: List[str]):
        self.code_roots = code_roots

    def retrieve(self, op:OpData, stack_map: Dict[str, any]) -> Optional[Dict]:
        """
        检索算子的源代码

        优先级：
          1. 通过 stack.json 中的调用栈信息检索
          2. 如果栈检索找到的文件中类名不匹配，尝试通过类名在代码仓中搜索
          3. 通过类名在代码中搜索

        Args:
            op: 算子数据
            stack_map: stack.json 的内容 (entry_id → [op_list, stack_list])

        Returns:
            {
                "op_name": str,
                "source_file": str,
                "source_code": str,    # 提取的类定义代码
                "method": "stack" | "class_search",
                "class_name": str
            } 或 None
        """
        result = self._retrieve_from_stack(op, stack_map)

        # 栈检索找到了代码但类名不匹配算子名中的类名 → fallback 到类名搜索
        if result and op.class_name:
            found_class = result.get("class_name", "")
            if found_class != op.class_name and op.class_name not in found_class:
                class_result = self._retrieve_by_class_name(op.class_name)
                if class_result:
                    class_result["method"] = "class_search"
                    return class_result

        if result:
            return result

        if op.class_name:
            class_result = self._retrieve_by_class_name(op.class_name)
            if class_result:
                class_result["method"] = "class_search"
                return class_result

        return None

    @staticmethod
    def _is_framework_or_tool_path(file_path: str) -> bool:
        """判断是否是框架或工具库路径，应该跳过"""
        skip_patterns = [
            "torch/", "torch_npu/",
            "msprobe/",
            "multiprocessing/",
            "python3.",  # Python 标准库路径
            "/site-packages/",  # pip 安装的第三方库（不含已知框架仓）
        ]
        path_lower = file_path.replace("\\", "/").lower()
        for pattern in skip_patterns:
            if pattern in path_lower:
                return True
        return False

    def _retrieve_from_stack(self, op: OpData,
                             stack_map: Dict[str, any]) -> Optional[Dict]:
        """通过 stack.json 的调用栈检索代码"""
        if not stack_map or not op.stack_entry:
            return None

        entry = stack_map.get(op.stack_entry)
        if not entry or len(entry) < 2:
            return None

        stack_lines = entry[1]
        if not stack_lines:
            return None

        # 从调用栈中提取文件路径和行号
        # 格式:"File /path/to/file.py, line 146, in function_name"
        # 从最底层（靠近算子的触发点）开始遍历
        for stack_line in reversed(stack_lines):
            file_path, line_no = self._parse_stack_line(stack_line)
            if not file_path or not line_no:
                continue

            # 跳过框架/工具库路径（torch、msprobe、multiprocessing 等）
            if self._is_framework_or_tool_path(file_path):
                continue

            code_result = self._read_code_from_file(file_path, line_no)
            if not code_result:
                continue

            # 关键：找到 class 后，必须和算子名中的类名比对，匹配才返回
            found_class = code_result.get("class_name", "")
            if op.class_name and found_class == op.class_name:
                return {
                    "op_name": op.full_name,
                    "source_file": file_path,
                    "source_code": code_result["code"],
                    "class_name": found_class,
                    "method": "stack",
                }

        # 严格匹配没找到，由外层的 retrieve 方法 fallback 到类名搜索
        return None

    def _parse_stack_line(self, line: str) -> Tuple[Optional[str], Optional[int]]:
        """解析 stack 行，提取文件路径和行号"""
        # 格式: "File /path/to/file.py, line146, in function_name"
        match = re.search(r'File\s+(.+?),\s*line\s+(\d+)', line)
        if match:
            return match.group(1).strip(), int(match.group(2))
        return None, None

    def _read_code_from_file(self, file_path: str,
                             line_no: int) -> Optional[Dict]:
        """
        从文件路径读取代码，找到类定义并提取类代码

        支持两种模式：
          1. 容器路径模式: 文件路径直接可用
          2. 代码仓映射模式: 提取相对路径，在 code_roots 中搜索
        """
        # 模式 1: 直接读取
        if os.path.exists(file_path):
            return self._extract_class_code(file_path, line_no)

        # 模式 2: 在代码仓中搜索
        if self.code_roots:
            relative_path = self._make_relative_path(file_path)
            for root in self.code_roots:
                for candidate in self._find_file(relative_path, root):
                    result = self._extract_class_code(candidate, line_no)
                    if result:
                        return result

        return None

    @staticmethod
    def _get_repo_dirs(code_roots: List[str]) -> List[str]:
        """从 code_roots 中提取代码仓目录名列表"""
        dirs = []
        for root in code_roots:
            name = os.path.basename(os.path.normpath(root))
            if name:
                dirs.append(name)
        return dirs

    def _make_relative_path(self, file_path: str) -> str:
        """
        从容器的 stack 路径中提取代名仓内相对路径。

        通过已知的代码仓目录名在路径中定位，提取其后部分作为相对路径。
        /home/.../vllm-ascend/vllm_ascend/ops/linear.py  +  code_root=vllm-ascend
        → vllm_ascend/ops/linear.py
        """
        known_repos = self._get_repo_dirs(self.code_roots)
        # 也兜底匹配常见框架名
        known_repos.extend(["vllm", "vllm-ascend", "verl", "msprobe", "mindspore", "transformers"])

        parts = file_path.replace("\\", "/").split("/")
        for i, part in enumerate(parts):
            if part in known_repos and i + 1 < len(parts):
                return "/".join(parts[i + 1:])

        # 兜底：取后 3 段
        return "/".join(parts[-3:]) if len(parts) >= 3 else file_path

    def _find_file(self, relative_path: str, code_root:str) -> List[str]:
        """通过相对路径在代码仓根目录下直接拼接查找，不再 os.walk"""
        if not os.path.isdir(code_root):
            return []
        full_path = os.path.join(code_root, relative_path)
        if os.path.exists(full_path):
            return [full_path]
        return []

    def _extract_class_code(self, file_path: str,
                            line_no: int) -> Optional[Dict]:
        """
        从文件的指定行号开始，向上查找类定义并提取代码

        Args:
            file_path: Python 文件路径
            line_no: 行号（1-indexed）

        Returns:
            {"code": 类定义源代码, "class_name": 类名}
        """
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except Exception:
            return None

        return self._extract_class_from_lines(lines, line_no)

    def _extract_class_from_lines(self, lines: List[str],
                                  line_no: int) -> Optional[Dict]:
        """从行列表中提取类定义"""
        if not lines or line_no < 1 or line_no > len(lines):
            return None

        class_start = -1
        class_name = None
        indent_level = 0

        # 从 line_no 向上搜索 class 定义
        for i in range(min(line_no - 1, len(lines) - 1), -1, -1):
            stripped = lines[i].strip()
            match = re.match(r"^\s*class\s+(\w+)", stripped)
            if match:
                class_start = i
                class_name = match.group(1)
                indent_level = len(lines[i]) - len(lines[i].lstrip())
                break

        if class_start < 0:
            return None

        # 提取类定义 (从 class 行到下一个同缩进级别的顶层定义)
        class_lines = []
        for i in range(class_start, len(lines)):
            line = lines[i]
            if i > class_start:
                stripped = line.strip()
                if stripped and not stripped.startswith(("#", "\n", " ")):
                    current_indent = len(line) - len(line.lstrip())
                    if current_indent <= indent_level:
                        break
                elif stripped and not stripped.startswith(("#", "\n")):
                    current_indent = len(line) - len(line.lstrip())
                    if current_indent <= indent_level:
                        break
            class_lines.append(line)

        return {
            "code": "".join(class_lines).strip(),
            "class_name": class_name,
        }

    def _retrieve_by_class_name(self, class_name: str) -> Optional[Dict]:
        """通过类名在代码仓中搜索类定义"""
        if not self.code_roots or not class_name:
            return None

        for root in self.code_roots:
            if not os.path.isdir(root):
                continue

            for dirpath, _, filenames in os.walk(root):
                # 跳过 __pycache__ 和 .git 等目录
                if "__pycache__" in dirpath or ".git" in dirpath:
                    continue

                for fname in filenames:
                    if not fname.endswith(".py"):
                        continue

                    file_path = os.path.join(dirpath, fname)
                    try:
                        with open(file_path, "r", encoding="utf-8",
                                  errors="ignore") as f:
                            content = f.read()

                        # 分别匹配 class 定义和 class 调用
                        pattern = rf"class\s+{re.escape(class_name)}\s*[:\(]"
                        if re.search(pattern, content):
                            lines = content.split("\n")
                            # 找到匹配的行号
                            for i, line in enumerate(lines):
                                if re.search(pattern, line):
                                    result = self._extract_class_from_lines(
                                        lines, i + 1
                                    )
                                    if result:
                                        result["op_name"] = class_name
                                        result["source_file"] = file_path
                                        return result
                    except Exception:
                        continue

        return None

    def batch_retrieve(self, ops: List[OpData],
                       stack_map: Dict[str, any]) -> Dict[str, Optional[Dict]]:
        """
        批量检索多个算子的代码

        Returns:
            op_name → code_result 或 None
        """
        results = {}
        for op in ops:
            result = self.retrieve(op, stack_map)
            results[op.full_name] = result
        return results
