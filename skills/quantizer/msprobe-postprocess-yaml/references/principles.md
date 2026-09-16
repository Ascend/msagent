# msmodelslim 量化变换原理与逆操作推导

适用场景：msmodelslim 量化（QuaRot 旋转 / fuse_ln / flex_smooth / W8A8 dynamic 等）后的模型，
用 msprobe dump 了量化模型与浮点模型的激活，需要先在量化侧激活上**逆转量化引入的变换**，再比对。

## 1. msprobe 后处理机制（ground truth）

- 比对命令（tensor 后处理配置经 `--config` 传入）：
  `msprobe compare -tp <量化dump>/dump.json -gp <浮点dump>/dump.json -o ./output --config matmul.yaml`
- 矩阵文件只认 `.npy` / `.pt` / `.pth`（`_load_tensor_as_numpy`）；
  加载失败时 `_try_matmul` 捕获异常后**原样返回 tensor，只有一行 warning 日志——静默失败**。
- YAML 结构（源码 matmul.yaml 模板 + 真实可用配置双重确认）：
  ```yaml
  right_matmul:
    target_tensor_map:      # 只作用于 target（量化侧）tensor
      /abs/path/Rt.npy:     # 矩阵绝对路径
      - Module.model....pt  # data_name 列表
    golden_tensor_map: {}   # 浮点侧不处理，留空
  ```
  另有 left_matmul（`mat @ value`）与 left_right_matmul（`Ml @ value @ Mr`）两种模式，
  键名为 left_/right_ 前缀 + target/golden_tensor_map；QuaRot 逆转用 right_matmul 即可。
  未知配置键会 WARN 并忽略（`_warn_unknown_config_keys`）。
- 没有配置的 data_name 原样通过（`mat_path is None → return value`）。
- **msprobe 不懂旋转**：给它 R 或 R^T 都会照乘不误且无报错。方向正确性必须在配置外自行验证（见 §6）。

## 2. QuaRot 离线旋转的数学（为何逆转 = 右乘 R^T）

设 Linear 前向 `y = x @ W^T`，R 正交（R R^T = I）：

| 操作 | 权重改写 | 激活侧效果 |
|---|---|---|
| 读侧补偿（right） | `W' = W @ R` | 输入被旋转：`x' = x @ R` |
| 写侧补偿（left） | `W' = R^T @ W`（bias 同步 `R^T b`） | 输出自动落在旋转基 |
| 全局方案 | embed 右旋 + head 右旋 + 所有读残差流的层右旋 + 所有写回残差流的层左旋 | 残差流全程 `x @ R`，logits 与原模型一致 |

等价性：`(xR)(WR)^T = x R R^T W^T = x @ W^T`。

因此量化模型 dump 出的残差流激活是 `x @ R`，逆转就是右乘 `R^T`（注意：**必须转置**。
R = diag(±1)@Hadamard 通常不对称，`|R - R^T|` 明显非零，不能拿 R 直接右乘）。

`quarot.safetensors` 只有一个 tensor：`global_rotation [hidden, hidden] F32`，
且 `quant_model_description.json` 末尾有 `optional.quarot.rotation_map` 指向它。

## 3. fuse_ln 与 flex_smooth 引入的额外缩放（为什么一个 R^T 不够）

msmodelslim 流程：`quarot → flex_smooth_quant → linear_quant`。

1. **fuse_ln**：RMSNorm 的 weight w 被烘焙进下游 Linear 权重，量化模型的 norm weight 全为 1。
   ⇒ 量化侧 LN 输出 = `Norm(x) @ R`（w 已不在激活里）；浮点侧 LN 输出 = `Norm(x) · w`。
2. **flex_smooth_quant**：smooth scale s 乘进 Linear 权重，`1/s` 乘进 norm weight
   （`s = 1 / 量化checkpoint的norm.weight`）。
   ⇒ 量化侧 LN 输出 = `Norm(x) @ R · diag(1/s)`。

所以 LN 输出类 tensor 的逆转矩阵是**复合矩阵**：

```
M_LN = diag(s) @ R^T @ diag(w)      # 右乘它：Norm(x)@R·diag(1/s) @ M_LN = Norm(x)·diag(w)（浮点语义）
```

只右乘 R^T 是错的：scale 卡在两个正交阵中间，`R·diag(1/s)·R^T` 不是任何有意义的量。

特殊：q_lora 空间（低秩 q 路径）的 LN（如 q_norm）用 R_b 替代 R：
`M_q = diag(s_q) @ R_b^T @ diag(w_q)`。R_b = `create_rot(BLOCK_HADAMARD_SHIFTED, q_lora_rank, block_size=32)`
（seed=1234，从未落盘，只烘焙进 wq_a/wq_b），可用 msmodelslim 源码复现后转成 npy。

## 4. dump data_name 的结构与内容门控

PyTorch dump（task=tensor，Module 级）：`Module.<module_path>.<class>.forward.<id>.<input|output>.<idx>.pt`

例：`Module.model.layers.0.input_layernorm.DeepseekV2RMSNorm.forward.0.output.0.pt`

**命名随框架/算子版本漂移**（class 名如 `AscendRMSNorm`/`DeepseekV2RMSNorm`、DecoderLayer
的 arg 索引随 vLLM/MindIE 版本增减、hidden 不总在 input.1），所以：

- **pattern 只按 module path 语义选候选**（class 名一律 `\w+`/`[\w.]+` 通配，arg 索引不写死）
- **真正的空间判定靠 dump.json 里自带的元数据做内容门控**（generate 脚本已实现）：
  - `dtype` 必须是 float/bfloat（挡掉 int64 的 positions / mask / token id）
  - `shape[-1]` 必须等于目标矩阵维度（挡掉 q_norm 1024 / kv_norm 512 / gate logits /
    heads×head_dim 等低维空间——它们匹配了路径但不在 R 空间）
  - `ndim >= 2`
  - 元数据缺失（纯名字列表）时门控退化为 no-op，覆盖报告会少一道保险
- dump.json 里名字嵌在 `data/<module>/{input_args,input_kwargs,output}/.../data_name`，
  旁边就有 dtype/shape，脚本递归提取

人工核对时：`input.0` 常是 `self` 之外的第一实参（可能是 positions），hidden 通常在
input.1+；**用 inspect 子命令看每个 module path 的 dtype/shape 分布**再下结论。

## 5. 激活空间归属速查（DeepSeek 系，QuaRot W8A8，按空间维度判别）

| dump 位置 | 量化侧值 | 逆转矩阵 | 维度 |
|---|---|---|---|
| embed 输出、DecoderLayer hidden 输入/输出、各 LN **输入**、attn 输出、wo_b 输出、最终 norm **输入** | `x @ R` | `R^T` | hidden |
| input_layernorm **输出**、dsa_attn.input.1、indexer 输入 | `Norm(x)@R·diag(1/s)` | `diag(s)@R^T@diag(w)` | hidden |
| post_attention_layernorm **输出**、MoE/FFN 输入、experts w1/w3 输入 | 同上 | 同上（ffn 的 s） | hidden |
| 最终 norm **输出**（logits 输入） | `Norm(x)@R` | `R^T@diag(w_norm)` | hidden |
| q_norm 输入/输出（q_lora 空间） | `·R_b` | `diag(s_q)@R_b^T@diag(w_q)` | q_lora_rank |
| kv_norm 输出 | 未旋转 | 不配置，直接比 | 512 |
| wo_b **输入**、logits 输出、gate 输出 | 未旋转/已还原 | 不配置，直接比 | 各异 |

（同一个 module path 下可能混着多个空间——如 self_attn 下既有 hidden 输出也有 512 维
kv_norm、64 维 indexer 输出——这正是内容门控按维度筛的原因。）

判断新位置归属的两条路线（建议都做）：
1. **前向代码（ground truth）**：读模型 forward，沿数据流追踪——旋转都烘焙在权重上，
   遇到右旋权重前的激活都在旋转基，穿过左旋写回层后回到旋转基。
2. **数值取证（交叉验证）**：比对 dump 值，如 `dsa_attn.input.1 ≡ input_layernorm.output`（逐位相等）
   则同空间；`lp_in` 与 norm 输出 cos=0.27 ⇒ 来源不明（warmup/MTP 数据），宁可不配。

## 6. 验证清单（生成配置后必须做）

1. **格式**：所有矩阵是 `.npy`/`.pt`；YAML 里写绝对路径；`TensorPostpostManager` 加载无 warning。
2. **正交性**：`Rt` 满足 `R @ R^T ≈ I`（范数保持检查：`(x@R)@R^T ≈ x`）。
   注意：范数不变**不能**发现方向错误（右乘 R 范数同样不变）。
3. **方向（关键）**：权重级等式验证，例如只左旋的层 `W_quant ≈ R^T @ W_float`（wo_b 误差 0.3%），
   由此唯一确定逆转方向是 R^T；wq_a 全链：`W_q ≈ R_b^T·(W_f·w)·R·diag(s)`。
4. **覆盖**：`coverage: N/M data names mapped`；比对结果里被映射的条目与 dump 名逐一对得上。
5. **端到端抽样**：任取一个已映射 tensor，`(dump值 @ 矩阵)` 与浮点侧 dump 值 cos ≈ 1。
6. 比对命令：`msprobe compare -tp <量化dump>/dump.json -gp <浮点dump>/dump.json -o ./output --config <yaml>`
   （仅真实数据模式，dump 时 task=tensor）。