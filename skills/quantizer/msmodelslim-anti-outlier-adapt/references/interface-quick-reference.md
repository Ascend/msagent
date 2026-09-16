# 离群值抑制接口快速参考

本文件是实现新模型离群值抑制 Adapter 的第一参考。它以当前安装的
msModelSlim 接口和目标模型当前的真实 forward 为准，提供一个可以直接改名和删减的
最小实现。不要从历史提交、其它模型的完整 Adapter 或模型名称推断映射；只有在当前
msModelSlim 源码缺少契约信息时才扩大检索范围。

## 先确定读取顺序

按下面的顺序完成适配，通常不需要阅读其它模型提交：

1. 查看当前环境的 `QuaRotInterface`、所选 Smooth 接口、`AdapterConfig` 和处理器的
   subgraph dispatch，确认实际签名和当前版本字段。
2. 阅读目标模型的 `config.json`、实际 `modeling_*.py` 和 Adapter 的模型加载/逐层
   forward，列出真实的 module path、hidden/head 维度、融合方式和 MoE 本地专家范围。
3. 用真实 forward 或官方 `fast_ops_grapher` 确认数据流，再填写下面的映射。
4. 在 msModelSlim 源码中实现接口，运行 `bash install.sh`，再用本 Skill 的接口检查脚本
   验证源码和已安装包一致。

参考的当前源码位置（路径随 msModelSlim checkout 变化时，以实际文件为准）：

```text
msmodelslim/processor/quarot/offline_quarot/quarot_interface.py
msmodelslim/processor/anti_outlier/common/subgraph_type.py
msmodelslim/core/graph/adapter_types.py
msmodelslim/processor/anti_outlier/flex_smooth/interface.py
msmodelslim/processor/anti_outlier/iter_smooth/interface.py
msmodelslim/processor/anti_outlier/flex_smooth/processor.py
msmodelslim/processor/anti_outlier/iter_smooth/processor.py
```

## 接口和算法对应关系

| 算法 | Adapter 必须显式继承 | 必须实现的方法 |
| --- | --- | --- |
| `quarot` | `QuaRotInterface` | `get_ln_fuse_map`、`get_bake_names`、`get_rotate_map(block_size)` |
| `flex_smooth_quant` | `FlexSmoothQuantInterface` | `get_adapter_config_for_subgraph()` |
| `flex_awq_ssz` | `FlexSmoothQuantInterface` | `get_adapter_config_for_subgraph()` |
| `iter_smooth` | `IterSmoothInterface` | `get_adapter_config_for_subgraph()` |
| `smooth_quant` | `SmoothQuantInterface` | `get_adapter_config_for_subgraph()` |
| `oasq` | `OASQInterface` | `get_adapter_config_for_subgraph()` |

当前 Skill 的默认门禁只运行前四项。后两项的映射结构相同，但不要因此把它们加入本
Skill 的默认算法列表。

多个 Smooth 算法共用一份映射时，Adapter 需要显式继承每个实际使用的接口。处理器用
`isinstance` 判断能力，不能只靠 `__getattr__` 转发方法：

```python
from msmodelslim.model.interface_hub import (
    FlexSmoothQuantInterface,
    IterSmoothInterface,
    QuaRotInterface,
)


class MyModelAdapter(
    ExistingModelAdapter,
    QuaRotInterface,
    FlexSmoothQuantInterface,
    IterSmoothInterface,
):
    ...
```

如果只验证 `flex_smooth_quant`，就只继承 `FlexSmoothQuantInterface`；不要为了“看起来
完整”继承不需要的接口。

`flex_awq_ssz` 仍然使用 `FlexSmoothQuantInterface` 和同一份拓扑映射；它额外要求处理器
配置中的官方 `qconfig`，这不改变 Adapter 接口。

## Smooth 系列的最小实现

`get_adapter_config_for_subgraph()` 返回 `List[AdapterConfig]`。路径必须是当前模型可以
通过 `model.get_submodule(path)` 找到的完整路径；不能使用只在某个示例模型中成立的
别名。下面是标准 decoder block 的最小结构模板，逐项替换为目标模型真实字段：

```python
from typing import List

from msmodelslim.core.graph.adapter_types import AdapterConfig, MappingConfig


class MyModelAdapter(ExistingModelAdapter, FlexSmoothQuantInterface, IterSmoothInterface):
    def get_adapter_config_for_subgraph(self) -> List[AdapterConfig]:
        configs: List[AdapterConfig] = []

        for layer_idx in range(self.config.num_hidden_layers):
            prefix = f"model.layers.{layer_idx}"

            # norm(x) -> [q(x), k(x), v(x)]
            configs.append(
                AdapterConfig(
                    subgraph_type="norm-linear",
                    mapping=MappingConfig(
                        source=f"{prefix}.input_layernorm",
                        targets=[
                            f"{prefix}.self_attn.q_proj",
                            f"{prefix}.self_attn.k_proj",
                            f"{prefix}.self_attn.v_proj",
                        ],
                    ),
                )
            )

            # post_norm(x) -> [gate(x), up(x)]
            configs.append(
                AdapterConfig(
                    subgraph_type="norm-linear",
                    mapping=MappingConfig(
                        source=f"{prefix}.post_attention_layernorm",
                        targets=[
                            f"{prefix}.mlp.gate_proj",
                            f"{prefix}.mlp.up_proj",
                        ],
                    ),
                )
            )

            # V -> O；只在真实 forward 中 O 的输入确实来自 V 时添加
            configs.append(
                AdapterConfig(
                    subgraph_type="ov",
                    mapping=MappingConfig(
                        source=f"{prefix}.self_attn.v_proj",
                        targets=[f"{prefix}.self_attn.o_proj"],
                    ),
                    extra_config={"group_method": "max"},
                )
            )

            # up -> down；gate 不放在 up-down mapping 中
            configs.append(
                AdapterConfig(
                    subgraph_type="up-down",
                    mapping=MappingConfig(
                        source=f"{prefix}.mlp.up_proj",
                        targets=[f"{prefix}.mlp.down_proj"],
                    ),
                )
            )

        return configs
```

四种 subgraph 的填写规则如下：

| `subgraph_type` | `mapping.source` | `mapping.targets` | 判断依据 |
| --- | --- | --- | --- |
| `norm-linear` | norm | 该 norm 输出直接进入的一个或多个 Linear | 同一归一化输出共同驱动这些 Linear |
| `up-down` | up projection | down projection | 激活函数/门控之后形成 up-down 数据流 |
| `linear-linear` | 第一层 Linear | 第二层 Linear | 第一层输出直接作为第二层输入 |
| `ov` | V 或等价 V 分支 | O projection | attention 中 V 的输出经过 head reshape/聚合进入 O |

不要把整个 block 的所有 Linear 放进一条 mapping。每条 mapping 只表达一个处理器支持
的子图；`targets` 必须非空。`norm-linear`、`linear-linear` 和 `up-down` 的通道维必须
按对应 forward 对齐；`ov` 的 head 分组/扩展由 `num_attention_heads`、
`num_key_value_heads` 和融合元数据共同决定。没有可融合 source
的非融合配置才使用 `source=None`，并把所有直接共享激活的 Linear 放进 `targets`。

### 融合 QKV、KV 或 MLA

当模型把 QKV/KV 融成一个权重时，仍然表达实际的 V→O 关系，并补充融合元数据；不要
把融合权重拆成虚构的 module path：

```python
from msmodelslim.core.graph.adapter_types import FusionConfig

AdapterConfig(
    subgraph_type="ov",
    mapping=MappingConfig(
        source="model.layers.0.self_attn.qkv_proj",  # 真实融合模块
        targets=["model.layers.0.self_attn.o_proj"],
    ),
    fusion=FusionConfig(
        fusion_type="qkv",
        num_attention_heads=self.config.num_attention_heads,
        num_key_value_heads=self.config.num_key_value_heads,
    ),
)
```

KV/MLA 只有在当前处理器要求时才使用 `fusion_type="kv"`，并提供真实的
`qk_nope_head_dim`、`v_head_dim` 等字段：

```python
FusionConfig(
    fusion_type="kv",
    num_attention_heads=self.config.num_attention_heads,
    num_key_value_heads=self.config.num_key_value_heads,
    custom_config={
        "qk_nope_head_dim": self.config.qk_nope_head_dim,
        "v_head_dim": self.config.v_head_dim,
    },
)
```

GQA/MQA 的 `num_attention_heads`、`num_key_value_heads` 必须来自当前模型配置。不要把
KV head 数当成 query head 数，也不要用字符串名称猜测融合类型。

### MoE 和动态分支

对 dense、shared expert、routed expert、router 分开生成 mapping。EP 场景下 routed
expert 只遍历当前 rank 已 materialize 的 expert 区间；smooth、QuaRot 的 mapping 和
权重加载必须使用同一个本地 expert 集合。未被本次校准输入激活的 expert 可以没有统计，
但不能用一个不存在的路径假装覆盖它。

VLM、MTP 或辅助 decoder 也按实际残差流分别列出路径。视觉 encoder 不自动当成 decoder
layer；只有视觉 merger 输出确实进入语言残差流时，才把它纳入相关 norm/linear 关系。

## QuaRot 的最小实现

QuaRot 的三个返回值含义固定：

```text
get_ln_fuse_map()  -> (pre_run_fused_ln, fused_map)
get_bake_names()   -> (pre_run_bake_names, bake_names)
get_rotate_map(block_size) -> (pre_run_pairs, rotate_pairs)
```

每个 rotation pair 都是 `RotatePair(left_rot: dict, right_rot: dict)`。字典 key 是
真实 module path，value 是与目标权重通道维相同的旋转矩阵（拼接子空间时也可以是当前
processor 支持的矩阵列表）。一个标准 decoder 的骨架如下：

```python
from msmodelslim.processor.quarot import QuaRotInterface


class MyModelAdapter(ExistingModelAdapter, QuaRotInterface):
    def get_ln_fuse_map(self):
        fused_map = {}
        for layer_idx in range(self.config.num_hidden_layers):
            prefix = f"model.layers.{layer_idx}"
            fused_map[f"{prefix}.input_layernorm"] = [
                f"{prefix}.self_attn.q_proj",
                f"{prefix}.self_attn.k_proj",
                f"{prefix}.self_attn.v_proj",
            ]
            fused_map[f"{prefix}.post_attention_layernorm"] = [
                f"{prefix}.mlp.gate_proj",
                f"{prefix}.mlp.up_proj",
            ]
        fused_map["model.norm"] = ["lm_head"]
        return {}, fused_map

    def get_bake_names(self):
        # RMSNorm 通常不需要 mean bake；LayerNorm 要以当前处理器和 forward 证据为准。
        return [], []

    def get_rotate_map(self, block_size):
        hidden_rot = self.get_rotate_command(
            mode=self.QuaRotMode.HADAMARD,
            size=self.config.hidden_size,
            block_size=block_size,
        )
        head_dim = getattr(self.config, "head_dim", None)
        if head_dim is None:
            head_dim = self.config.hidden_size // self.config.num_attention_heads
        head_rot = self.get_rotate_command(
            mode=self.QuaRotMode.HADAMARD,
            size=head_dim,
            block_size=block_size,
        )

        pre_run = self.RotatePair(
            left_rot={},
            right_rot={"model.embed_tokens": hidden_rot},
        )

        left_rot = {}
        right_rot = {"lm_head": hidden_rot}
        for layer_idx in range(self.config.num_hidden_layers):
            prefix = f"model.layers.{layer_idx}"
            right_rot.update(
                {
                    f"{prefix}.self_attn.q_proj": hidden_rot,
                    f"{prefix}.self_attn.k_proj": hidden_rot,
                    f"{prefix}.self_attn.v_proj": hidden_rot,
                    f"{prefix}.mlp.gate_proj": hidden_rot,
                    f"{prefix}.mlp.up_proj": hidden_rot,
                }
            )
            left_rot.update(
                {
                    f"{prefix}.self_attn.o_proj": hidden_rot,
                    f"{prefix}.mlp.down_proj": hidden_rot,
                }
            )

        main = self.RotatePair(left_rot=left_rot, right_rot=right_rot)

        # 只有 V/O 的 head 子空间确实独立且维度匹配时才保留这一 pair。
        ov_left = {}
        ov_right = {}
        for layer_idx in range(self.config.num_hidden_layers):
            prefix = f"model.layers.{layer_idx}"
            ov_left[f"{prefix}.self_attn.v_proj"] = head_rot
            ov_right[f"{prefix}.self_attn.o_proj"] = head_rot
        ov = self.RotatePair(left_rot=ov_left, right_rot=ov_right)
        return [pre_run], [main, ov]
```

`left`/`right` 不是可以凭名称猜的装饰项。当前 QuaRot processor 对 Linear 权重分别
沿输出维和输入维执行旋转；应先查看目标权重 shape 和真实 tensor 流，再决定一侧和
矩阵大小。`model.embed_tokens`、`lm_head`、Q/K/V、O、MLP projection 的组合只是标准
decoder 示例。融合 QKV、低秩 MLA、拼接输出、MTP 和专家 projection 必须按实际分块构造
矩阵；不兼容的子空间不旋转。

`get_bake_names()` 只用于 mean bake。没有 LayerNorm/mean 变换证据时保持空列表；不能
把 LayerNorm 的规则套到 RMSNorm。所有 key 都必须能被当前模型解析，重复 key 或同一权重
同时收到冲突旋转应在运行前报错。

## 最小验证闭环

实现后只做以下闭环即可判断接口是否可用：

```bash
bash install.sh

python skills/quantizer/msmodelslim-anti-outlier-adapt/scripts/validate_anti_outlier_interfaces.py \
  --model-type <model_type> \
  --model-path <checkpoint> \
  --source-root <msmodelslim_checkout> \
  --output-dir <workdir> \
  --algorithm quarot \
  --algorithm flex_smooth_quant \
  --algorithm iter_smooth
```

检查结果必须同时满足：

- Adapter 显式实现了所选接口，且源码文件 hash 与已安装实现一致；
- Smooth 返回非空 `AdapterConfig`，每条 subgraph 类型受当前 processor 支持，source/target
  路径存在且顺序符合 forward；
- QuaRot 三个 map 的返回层级正确，矩阵 shape 与目标权重通道一致；
- 只对当前 checkpoint 和当前输入覆盖的分支报告 PASS；动态分支未覆盖就记录限制；
- 之后再执行本 Skill 的单 processor、before/after final-logits 门禁。

常见错误与修复方向：

| 现象 | 优先检查 |
| --- | --- |
| `isinstance` 失败或处理器回退自动发现 | Adapter 是否显式继承所选 interface |
| Smooth 返回空结果或全部 skip | `AdapterConfig` 是否非空、路径是否真实、目标是否收到校准激活 |
| `norm-linear` shape 错误 | norm 输出维度与每个 target 的输入维度是否一致 |
| `ov` shape/分组错误 | V/O 的 head 数、KV head 数、融合元数据和 head dim |
| QuaRot rotation shape 错误 | left/right 方向、权重布局、block size 和旋转子空间 |
| QuaRot 运行后 logits 大幅漂移 | 漏掉 embedding/head/残差路径，或把不兼容分支强行共用 hidden rotation |
| MoE 只有部分 rank 失败 | mapping、权重加载和 forward 是否使用同一 `local_expert_ids` |
