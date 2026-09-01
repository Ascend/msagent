---
name: module-name-mapper
description: 对比 error/normal 的 dump 数据，加载三份 JSON （dump.json + construct.json + stack.json），执行四级降级模块名称映射（Level 0-3），输出结构化映射结果。适用场景：任何需要先对齐两侧模块名称再进行后续精度分析的任务，如同框架精度对比、GPU→NPU 迁移、框架升级等。
---

# Module Name Mapper - 模块名称映射 Skill

## 输入

用户提供以下内容：

- **`error_dump/` 目录**（含 `dump.json` + `construct.json` + `stack.json`）
- **`normal_dump/` 目录**（含 `dump.json` + `construct.json` + `stack.json`）
- **`--code-root`**（可选）：代码仓根目录路径，Level 3 代码对比需要
- **`--disable-level3`**（可选）：关闭 Level 3 代码对比
  
> **范围：本 skill 严格只做 Module 级（`Module.*`）模块名称映射**。加载两侧 dump 后即过滤掉所有非 `Module.*` 算子（`Tensor.*` / `NPU.*` / `Distributed.*` / `Functional.*` / `Torch.*` / `Triton.*` 不进入映射）。API 级算子的配对与对比由下游 `api-root-cause` 依据 construct 结构**自建**，不依赖本 skill 的 API 级结果——本 skill 只产出 Module 级映射。

## 输出

- `module_mapping_result.json`：结构化映射结果（含各级匹配统计、未匹配算子列表、融合模式统计）
- `module_mapping_report.md`：Markdown 报告（含 Level 3 分析章节）
- `level3_analysis.json`（仅 Level 3）：AI 对未匹配算子的代码对比分析记录，包括匹配依据、融合模式、代码引用
- `suggested_variant_additions.json`（仅 Level 3）：AI 建议的 module_path 变体映射规则，供用户审阅后选择性添加到 `name_variant_map.json`
- `cell_mapping.yaml`（对接 msprobe 精度比对）：用于 msprobe `compare ... -cm cell_mapping.yaml` 的「不同平台不同配置下的模块对比」，由 `generate_cell_mapping.py` 从 `matched_pairs` 生成（见 Step 6）

## 四级降级映射策略

### Level 0: full_name 精确匹配
- **匹配 key**: `full_name`（完整算子名）
- **方式**: 基于集合的查找，不要求位置对应
- **作用**: 同名算子直接对齐，最快最准确

### Level 1: 模块路径精确匹配
- **匹配 key**: `(op_type, module_path, layer_id, forward_number)`
- **方式**: 在 Level 0 划分的边界区间内独立匹配
- **作用**: 去掉类名差异后对齐（如 `AscendVocabParallelEmbedding` vs `VocabParallelEmbedding`）

### Level 2: 变体映射匹配
- **匹配 key**: `(op_type, apply_variants(module_path), layer_id, forward_number)`
- **方式**: 在 Level 1 划分的新边界区间内独立匹配
- **作用**: 覆盖模块路径的命名差异（如 `self_attn` vs `attn`、`mlp` vs `ffn`）

### Level 3: 代码对比匹配
- **方式**: 按类名归并后一次性检索代码，综合 prompt 判断
- **匹配 key**: 由大模型通过代码对比判断功能是否等价
- **作用**: 兜底，覆盖上述级别无法对齐算子

**每层完成后重新划定边界，下一层在缩小后的区间内操作。**

## 完整执行流程（大模型职责）

以下是大模型（即当前对话的 AI）使用此 skill 时应执行的完整步骤：

### Step 1: 确认输入

向用户确认以下信息，**顺序很重要**：
1. error_dump 和 normal_dump 的路径
2. 是否需要执行 Level 3（默认开启）
3. **如果用户开启 Level 3** → 追问代码仓路径（`--code-root`），得到路径后再往下执行
   **如果用户关闭 Level 3** → **不要询问**代码仓路径，直接进入 Step 2

> **必须显式询问 `--code-root`，不要自动探测**（例如不要自行递归搜索文件系统定位代码仓）。
> 开启 Level 3 时，先向用户确认代码仓路径再执行；未开启 Level 3 时无需确认代码仓路径。

### Step 2: 运行 Level 0+1+2

> **脚本路径**：以下命令使用**相对路径** `.msagent/skills/module-name-mapper/scripts/...`，该路径**相对于包含 `.msagent` 的目录**（即开源仓库根目录 / 用户项目根目录）。请**在含 `.msagent` 的目录（仓库根目录）下执行**，不要 `cd` 进 skill 目录——`--out-dir` 使用相对路径时，输出将落在**当前工作目录（用户打开的项目目录）** 下。

```bash
python .msagent/skills/module-name-mapper/scripts/main.py \
  --error-dump <error_dump_path> \
  --normal-dump <normal_dump_path> \
  --disable-level3 \
  --non-interactive \
  --out-dir results/module-mapping
```

- 读取输出的 `module_mapping_result.json` 和 `module_mapping_report.md`
- 检查覆盖率，评估是否需要 Level 3
- **注意**：此报告为 **Level 0+1+2 中间报告**。如果后续执行 Level 3，中间报告将被最终报告覆盖，请勿基于中间报告做结论

### Step 3: 执行 Level 3（代码对比）

Level 3 **不**依赖脚本的 `compare_zones` 逐区间检索。改为大模型直接分析未匹配模式，按类名归纳后做代码检索和综合判断。

**为什么这样做？**
- 实际数据中，未匹配算子的类名种类通常远少于算子总数（如 0728 中 305 个 error 未匹配算子只有 3 种类名）
- 逐区间检索在每层 61 个区间中重复检索相同类名，效率低
- 按类名检索一次即可覆盖所有层级的同类算子

#### Step 3.1: 分析未匹配模式

运行 Level 0+1+2 后，读取 `module_mapping_result.json`，分析 `unmatched_error` 和 `unmatched_normal` 列表，按类名归纳模式

用 Python 统计（不要手动数），一个可复用的命令模板：

```python
# 统计 error 侧未匹配算子按类名分布
from collections import Counter
error_counter = Counter()
for op in data.get('unmatched_error', []):
    parts = op['full_name'].split('.')
    cls = parts[-3] if len(parts) >= 3 else parts[-1]
    error_counter[cls] += 1
print("error 侧未匹配:", dict(error_counter))

# 统计 normal 侧未匹配算子按类名分布
normal_counter = Counter()
for op in data.get('unmatched_normal', []):
    parts = op['full_name'].split('.')
    cls = parts[-3] if len(parts) >= 3 else parts[-1]
    normal_counter[cls] += 1
print("normal 侧未匹配:", dict(normal_counter))
```

输出示例：
```
error 侧未匹配:
  AscendReplicatedLinear: 122
  AscendRMSNorm: 122
  AscendDeepseekSparseAttention: 61
  
normal 侧未匹配:
  MergedColumnParallelLinear: 122
  DeepseekCompressor: 91
  DeepseekV4MLAAttention: 61
  ...
```

#### Step 3.2: 按类名检索代码

**每个类名只检索一次**，不要逐区间重复检索。用 `CodeRetriever` 类（或直接读取代码仓文件）：

```python
from code_retriever import CodeRetriever

retriever = CodeRetriever(code_roots)
code = retriever._retrieve_by_class_name("AscendReplicatedLinear")
# 结果缓存后可用于所有层级的同名算子
```

需要检索的代码仓路径通过 `--code-root` 参数获得。

**检索优先级**：
1. 只检索 error 和 normal 两侧的**类名**（约 3-15 个类，不是每个算子）
2. 对每个类：读取`class` 定义、`__init__`、`forward` 方法
3. 如果 forward 是薄封装（只调了一个子函数），按 `assets/code_compare_prompt.md` 的展开规则决定是否展开
4. 对于两侧的关键容器类（如 DecoderLayer、MoE），了解其内部结构以判断哪些子模块是容器内细节

#### Step 3.3: 综合判断配对关系

读取所有类名对应的代码后，分析以下四种关系。**注意区分"真实对位算子"与"内部 kernel 片段"——只有两侧都存在可引用的独立算子时，才算成功匹配。**

- **1:1 等价**：两侧都是真实存在的**独立算子**，功能完全一致，仅命名不同或继承关系（如 `AscendRMSNorm` ↔ `RMSNorm`） → **计为匹配**
- **N:1 融合/拆分**：一侧拆分为多个算子，另一侧融合为一个，且融合后的算子在另一侧**确实作为独立算子存在于 dump** 中（如 `wq_a` + `wkv` ↔ `fused_wqa_wkv`，后者真实存在于 normal_dump）→ **计为匹配**（按 error 侧拆分后的实际算子数，见 Step 4）
- **1:0 absorbed（kernel 吸收）**：error 侧算子在 normal 侧**没有对应的独立算子**，仅等价于另一侧某个算子的**内部 kernel 一段逻辑**（如 `q_norm`/`kv_norm` 被吸收进 GPU MLA attention kernel，normal_dump 中没有单独的 norm 算子）→ **不计为匹配、不计入覆盖率**，但必须在报告与 `level3_analysis.json` 中**说明**（标记为 absorbed）
- **已通过上级容器匹配**：normal 侧子模块已在容器级（Level 2）匹配，无需重复处理

> **判断 absorbed 与 N:1 融合的关键差异**：融合的 normal 侧目标是**真实存在、可在 dump 中引用**的算子（`fused_wqa_wkv`）；absorbed 的 normal 侧目标**不存在于 dump**，只是一段 kernel 内部逻辑。**算子级匹配必须两侧都有可对位的独立算子**——若一侧算子只与另一侧某个算子的内部 kernel 功能等价，不能视为两侧对应算子等价，故不计匹配、不计覆盖率。

**关键判断依据**（不是简单看类名相似度）：
- 继承关系（`class A(Base)` vs `class B(Base)` → 等价）
- 模块路径对应（`self_attn.wq_a` vs `attn.fused_wqa_wkv` → 对应位置）
- 输入输出接口一致性
- 代码中确认子模块是否匹配

输出格式——保存为 `level3_analysis.json`：

```json
{
  "analysis_timestamp": "2026-08-03",
  "model": "DeepSeek V4",
  "level3_matches": [
    {
      "type": "1:1",
      "error_class": "AscendDeepseekSparseAttention",
      "normal_class": "DeepseekV4MultiHeadLatentAttentionWrapper",
      "count": 61,
      "confidence": "high",
      "reason": "继承同一基类 MultiHeadLatentAttentionWrapper，输入输出接口一致"
    }
  ],
  "fusion_patterns": [
    {
      "type": "N:1 (2→1 fusion)",
      "error_classes": ["AscendReplicatedLinear(wq_a)", "AscendReplicatedLinear(wkv)"],
      "normal_classes": ["MergedColumnParallelLinear(fused_wqa_wkv)"],
      "count": 61,
      "confidence": "high",
      "reason": "GPU 用 MergedColumnParallelLinear(output_sizes=[q_lora_rank, head_dim]) 融合 wq_a 和 wkv，NPU 保持分开"
    }
  ],
  "already_matched_as_submodules": [
    {
      "context": "FFN shared_experts 内部组件",
      "explanation": "gate_up_proj/act_fn/down_proj/DeepseekV4MLP 等已在容器级(DeepseekV4MoE↔DeepseekV2MLP)匹配"
    }
  ],
  "absorbed_operators": [
    {
      "type": "1:0 (norm absorbed into GPU kernel)",
      "error_class": "AscendRMSNorm",
      "submodule_path": "self_attn.q_norm",
      "count": 61,
      "confidence": "high",
      "reason": "NPU 侧显式导出为独立算子，GPU 侧被吸收进 MLA attention kernel 内部，normal_dump 中无独立算子可对位。功能等价但**不计匹配、不计覆盖率**。"
    }
  ]
}
```

#### Step 3.4: 检查是否已存在高级容器匹配

在应用 Level 3 匹配前，先检查 `module_mapping_result.json` 中 `matched_pairs` 是否已将某些高级容器（如 `DeepseekV4MoE`、`FusedMoE`）匹配。如果已匹配：
- 容器内部子模块（如 `gate_up_proj`、`act_fn`、`down_proj`）虽然在 unmatched 列表中，但属于**序列化粒度差异**（容器级 vs 子模块级）
- **不要**将这些子模块再次配对，在报告和 `level3_analysis.json` 中说明即可

### Step 4: 应用判断结果

**⚠️ 重要：使用独立 Python 脚本更新 JSON，不要手动拼凑或逐条编辑。脚本必须可重入。**
- 修改时**不得重命名或删除** `matched_pairs`、`unmatched_error`、`unmatched_normal` 这三个根级 list
- 用 `.extend()` 追加匹配，不要替换
- 修改后必须验证 JSON 结构完整性

**脚本存放位置**：该应用脚本与**本次测试的数据强绑定**（匹配规则耦合于具体模型/数据集），应**与本次结果的输出目录放在一起**，即放在 `--out-dir` 指定的目录下（如本次为 `results/module-mapping/`），并命名为 `apply_level3_matches.py`。**不要写死为 `results/module-mapping/`**——换一份测试数据时，`--out-dir` 会不同（如 `results/module-mapping-0729/`），脚本随对应结果走即可。该脚本是**数据强绑定的临时工具**，不是 skill 的固定组件，与 `.msagent/skills/module-name-mapper/scripts/` 下的通用组件区分。

> 下方的"推荐脚本模式"是通用模板（含占位符 `[...]`），按**本次实际模型**填入匹配规则后，保存到本次结果的输出目录（见上文"脚本存放位置"）使用；模板用 `out_dir` 推导结果路径，换数据时无需改路径。

推荐脚本模式：

```python
import json
import os

# 脚本与本 out_dir 下的结果放在一起，out_dir = --out-dir（本次为 results/module-mapping，换数据会不同）
out_dir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(out_dir, 'module_mapping_result.json'), encoding='utf-8') as f:
    data = json.load(f)

# ----- 构建 Level 3 匹配 -----
new_matches = [...]     # 从 level3_analysis.json 读取
error_matched = set()   # 已匹配的 error full_name
normal_matched = set()  # 已匹配的 normal full_name

# 追加到现有列表（不替换）
data['matched_pairs'].extend(new_matches)

# 从未匹配列表中移除
data['unmatched_error'] = [op for op in data['unmatched_error']
                           if op['full_name'] not in error_matched]
data['unmatched_normal'] = [op for op in data['unmatched_normal']
                            if op['full_name'] not in normal_matched]

# ----- 更新 summary（error 侧统一口径） -----
s = data['summary']
l0 = s['level0_matched']
l1 = s['level1_matched']
l2 = s['level2_matched']

# Level 3：1:1 匹配的 error 算子数 + 融合模式中 error 侧拆分后的实际算子数
# ⚠️ absorbed(1:0) 算子不计入 level3_matched，也不计入覆盖率（见 Step 3.3）
level3_error_total = level3_11_count + level3_fusion_error_count
absorbed_error_count = <absorbed 算子数，如 122>   # 仅说明，不计入匹配/覆盖率

# 总匹配 = 各级别 error 侧匹配算子数之和（absorbed 不参与）
matched_error_total = l0 + l1 + l2 + level3_error_total

s['level3_matched'] = level3_error_total
s['unmatched_error'] = len(data['unmatched_error'])
s['unmatched_normal'] = len(data['unmatched_normal'])
s['coverage'] = round(matched_error_total / s['total_error_operators'] * 100, 2)
s['matched_pairs'] = matched_error_total

# 各级占比（保留两位小数）
s['level0_pct'] = round(l0 / matched_error_total * 100, 2)
s['level1_pct'] = round(l1 / matched_error_total * 100, 2)
s['level2_pct'] = round(l2 / matched_error_total * 100, 2)
s['level3_pct'] = round(level3_error_total / matched_error_total * 100, 2)

# ----- 融合模式统计 -----
s['fusion_stats'] = {
    'patterns': [
        {
            'type': 'N:1 (2→1 fusion)',
            'error_side': {
                'module_paths': ['...'],
                'class_names': [...],
                'operator_count': <error侧算子数>,
                'per_layer': '每层 N 个'
            },
            'normal_side': {
                'module_path': '...',
                'class_name': '...',
                'operator_count': <normal侧算子数>,
                'per_layer': '每层 N 个'
            },
            'coverage_layers': '涉及层数范围',
            'confidence': 'high'
        }
    ],
    'note': '融合模式以 error 侧拆分后的实际算子数计入匹配，normal 侧以融合后的算子数计入',
    'absorbed_operators': [
        {
            'type': '1:0 (absorbed into GPU kernel)',
            'error_side': {
                'module_paths': ['self_attn.q_norm', 'self_attn.kv_norm'],
                'class_names': ['AscendRMSNorm'],
                'operator_count': 122,
                'per_layer': '每层 2 个'
            },
            'normal_side': {
                'note': 'GPU MLA attention kernel 内部吸收，normal_dump 中无独立算子可对位'
            },
            'coverage_layers': '0-60 (全部 61 层)',
            'confidence': 'high',
            'coverage_note': 'absorbed 算子不计入 matched_pairs、不计入覆盖率；unmatched_error 保留计数，但在报告/分析中说明为已分析(absorbed)'
        }
    ]
}

with open(os.path.join(out_dir, 'module_mapping_result.json'), 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
```

**脚本执行后，必须验证 JSON 结构完整性**：用以下命令确认 4 个核心字段都存在且类型正确：

```bash
python -c "
import json
with open('module_mapping_result.json') as f:   # 在脚本/结果所在目录下执行
    d = json.load(f)
assert isinstance(d['matched_pairs'], list), 'matched_pairs must be list'
assert isinstance(d['unmatched_error'], list), 'unmatched_error must be list'
assert isinstance(d['unmatched_normal'], list), 'unmatched_normal must be list'
assert isinstance(d['summary'], dict), 'summary must be dict'
print('JSON structure OK')
"
```

### Step 5: 输出最终结果

1. 更新 `module_mapping_report.md`：
   - 映射统计（匹配对数、覆盖率、各级占比）—— 覆盖率统一按 error 侧口径计算，百分比保留两位小数
   - 融合模式统计表（显示每种融合模式、两侧算子/层数、涉及层数）
   - Level 3 分析章节（含架构对比、代码引用、匹配依据）
   - 更新后的未匹配列表（按类名汇总展示，不要输出几百行的逐条表格）
   - 诊断建议

2. 生成 `suggested_variant_additions.json`：
   - **只关注 `op_type` 和 `apply_variants(module_path)` 的变体映射**
   - 不应包含类名字段的映射（Level 1 已去掉了类名的差异）
   - 格式：`{ "from": "self_attn", "to": "attn"}`（与 `name_variant_map.json` 的 variants 格式一致，可直接复制）
   - 融合模式（N:1 / 1:N）不适合变体映射（变体映射只支持 1:1 替换），在报告的 `fusion_notes` 中说明即可

3. 向用户展示最终映射统计

### Step 6: 生成 msprobe cell_mapping（对接精度比对）

对接 msprobe 的「不同平台不同配置下的模块对比」功能（`msprobe compare -tp ... -gp ... -o ... -cm cell_mapping.yaml`），把本 skill 的模块级映射结果直接喂给 msprobe 做精度比对。

```bash
python .msagent/skills/module-name-mapper/scripts/generate_cell_mapping.py \
  --result <out_dir>/module_mapping_result.json \
  --out <out_dir>/cell_mapping.yaml
```

- **粒度**：`{module_name}.{class_name}` 级（含层索引、不含 `Module.` 前缀），如 `model.layers.22.self_attn.wq_b.AscendColumnParallelLinear: model.layers.22.attn.wq_b.ColumnParallelLinear`。复刻 msprobe `process_cell_mapping` 的 cell_name 切分规则（去掉 `Module.`/`Cell.` 前缀 + 切到 `.forward/.backward/.parameters_grad` 之前）。
- **只处理 `matched_pairs`**；absorbed(1:0) 等未匹配算子不计匹配，**不进 cell_mapping**（与 skill 的"不计匹配"语义一致）。
- **使用「名称映射」格式（整段精确匹配），不做字符串子串映射**：名称映射已含完整路径+类名，一条即可精确覆盖；字符串子串映射有`replace(...,1)` 只替换首处、且可能误伤包含该子串的其他 cell 的风险。
- **验证**：生成后应仿真 `process_cell_mapping` 对 dump 中所有 `Module.*` 算子应用，确认改写后的 cell 在 golden dump 中真实存在、且不外溢（参考实测：733 对 100% 命中、0 缺失、0 误伤）。

## 关键优化指南

### 代码检索：按类名归并，不逐区间检索

- **不要**按脚本的 `compare_zones` 结果逐区间检索代码
- **应该**按类名统计后，每个类名只检索一次
- 效率对比：61 区间 × 3 类/区间 → 3 类（约 60x 减少）

### JSON 操作：用脚本，不用手动

- 任何修改结构化 JSON 的操作都通过独立 Python 脚本执行
- 脚本必须可重入（多次运行结果一致）
- 修改后必须验证 JSON 结构完整性

### Level 3 判断：先查容器匹配

- 有些 normal 侧子模块（gate_up_proj, act_fn, down_proj, GateLinear, DeepseekV4MLP 等）在 unmatched 列表中
- **先查** `matched_pairs` 中是否已有高级容器（MoE、MLP）的 Level 2 匹配
- 如果是 → 属于粒度差异，在报告中说明即可，**不要**在 Level 3 中再次匹配

### absorbed(1:0) 算子：说明但不计入

- 当 error 侧算子在 normal 侧**没有独立算子**、仅等价于另一侧某个算子的**内部 kernel 片段**时（如 norm 被吸收进 attention kernel），标记为 **absorbed**
- **不计入 matched_pairs、不计入覆盖率**；`unmatched_error` 保留计数，但在报告/`level3_analysis.json` 中说明为"已分析(absorbed)，功能等价但无独立算子可对位"
- 与 **N:1 融合** 严格区分：融合的 normal 侧目标必须是**真实存在于 dump 的独立算子**（如 `fused_wqa_wkv`），才计为匹配；absorbed 无此前提，故不计

## 命名变体映射表

映射规则存储在 `.msagent/skills/module-name-mapper/config/name_variant_map.json` 中。

用户可以根据实际场景添加新规则。Level 3 判断确认的映射对可以建议用户添加到该文件中。
注意：融合模式（N:1 / 1:N）不适合用变体映射表示，应在结果处理流程中特殊处理。

## 注意事项

- Level 0/1/2 由脚本自动完成，不需要大模型介入
- Level 3 需要大模型读取 prompt 文件并进行代码对比判断
- 判断时参照 `assets/code_compare_prompt.md` 中的对比维度和忽略项
- 最终输出给用户的结果应包括映射统计和建议
- **如脚本输出出现中文乱码，用 Python one-liner 读取 JSON 数据进行分析，不要依赖终端中文显示**
- **cell_mapping.yaml 对接 msprobe 精度对比（`-cm`）**：本 skill 只做 Module 级映射，因此产出的 cell_mapping 也只覆盖 Module 算子；API 级算子（`Functional.*`/`Tensor.*`等）的 cell_mapping 由下游 skill 或用户另行补充。cell_mapping 粒度与 msprobe `process_cell_mapping` 要求的 cell_name（去 `Module.` 前缀 + 切 forward/backward）严格对齐，生成后需完成 Step 6 的端到端验证。

