# 生成脚本参考：generate_postprocess.py

依赖：`numpy`、`pyyaml`、`safetensors`（读 quarot.safetensors / checkpoint 分片）；
`make-rb` 另外需要可导入的 msmodelslim（仓库或安装目录）。

## 脚本管不了的两件事（需要 TraeCode 自己做）

1. **前向代码定空间归属**：脚本只按 data_name 正则匹配，不认识新模型的数据流。
   用户提供模型前向代码（`inference/model.py` / HF `modeling_*.py`）时，沿数据流逐条
   核对 dump tensor 的旋转状态（旋转烘焙在权重上 → 遇右旋权重前都在旋转基）。
2. **msmodelslim 模型适配器定旋转点**：`msmodelslim/model/<model_type>/model_adapter.py`
   的 `get_rotate_map()` 列出哪些 Linear 被右旋/左旋、`create_rot` 的 seed/type 等参数。
   没有这两样时退回内置规则 + 数值取证（dump 值逐位比对/余弦）。

## 子命令

### convert-rot — 旋转矩阵转置转存

```bash
python generate_postprocess.py convert-rot \
  --rot /path/optional/quarot.safetensors [--key global_rotation] \
  --outdir /path/matrices [--name Rt] [--no-transpose]
```

- safetensors/npz/npy/pt → `<outdir>/<name>.npy`，默认**转置**（dump 是 `x@R`，right_matmul 右乘，需要 R^T）。
- 自动打印正交性报告（`max|R@R^T-diag|`、对称性）。非正交会 WARN。

### ln-matrices — 从两个 checkpoint 合成逐 LN 复合矩阵

```bash
python generate_postprocess.py ln-matrices \
  --float-ckpt /float/model_dir --quant-ckpt /quant/model_dir \
  --rot /path/optional/quarot.safetensors \
  [--rb /path/rb.npy --rb-key ... --rb-norm-regexp 'q_norm'] \
  [--norm-regexp 'norm\.weight$'] \
  --outdir /path/matrices
```

- 对每个两侧同名的 norm weight：`M = diag(1/quant_w) @ Rt @ diag(float_w)`（Rb 空间自动替换 Rt 为 Rb^T）。
- 写出 `model.layers.<i>.input_layernorm.npy`（norm key 的 stem）与 `ln_matrices.json`
  （记录每个矩阵的 dim / 是否带 smooth / 空间）。
- `--rb-norm-regexp` 命中的 norm 走 Rb 空间（如 q_norm，q_lora_rank 维）。

### make-rb — 复现从未落盘的 Rb（q_lora 空间旋转）

```bash
python generate_postprocess.py make-rb \
  --msmodelslim /path/to/msmodelslim_repo_or_install \
  --dim 1024            # q_lora_rank
  [--rot-type BLOCK_HADAMARD_SHIFTED] [--block-size 32] [--seed 1234] \
  --outdir /path/matrices
```

- Rb 从不被 msmodelslim 保存（只烘焙进 wq_a/wq_b 权重），但 `create_rot` 用固定 seed，
  相同参数可复现。脚本动态导入 msmodelslim 的 `create_rot`（按参数名自动映射
  size/type/block_size/seed），转置后存为 `Rb_t.npy`。
- `--msmodelslim` 接受：仓库根 / site-packages 目录 / `quarot_utils.py` 文件路径。
- **seed/type/block_size 必须与量化时一致**（deepseek 系默认 1234 / BLOCK_HADAMARD_SHIFTED / 32），
  生成后务必做权重级验证（quant wq_a ≈ Rb^T·(float wq_a · ln_w)·...，见 principles.md §6）。

### generate — 扫 dump.json 出 YAML

```bash
python generate_postprocess.py generate \
  --dump-json /quant_dump/rank0/dump.json \
  --matrices-dir /path/matrices \
  --output /path/postprocess.yaml \
  [--rules rules.yaml] [--extra-rule 'REGEX=MATRIX'] [--dry-run]
```

- 规则按顺序匹配，首个命中的生效；矩阵文件不存在的规则自动跳过（输出 WARN 列表）。
- **内容门控**：dump.json 自带每个 tensor 的 dtype/shape 元数据，路径匹配后还要过
  dtype=float/bfloat、`shape[-1]` == 矩阵维度、ndim>=2 三道门——挡掉 int64 positions、
  q_norm(1024)/kv_norm(512)/gate logits 等不在目标空间的 tensor。被拒名单会按原因打印。
- **pattern 设计原则**：class 名通配（`\w+`/`[\w.]+`）、arg 索引不写死——命名随
  框架/算子版本漂移（AscendRMSNorm vs DeepseekV2RMSNorm、hidden 不总在 input.1），
  空间判定交给门控而不是正则。
- 输出 schema 固定为真实可用配置的结构：`right_matmul: {target_tensor_map: {矩阵: [名字]}, golden_tensor_map: {}}`
  （只配 target 侧；浮点侧 golden 不做处理）。
- 先用 `--dry-run` 看覆盖报告（`coverage: N/M`）再落盘。
- **校准基准**：在一个真实 DeepSeek 系 w8a8 dump 上，默认规则 + 门控产出的名字集合
  与人工逐条验证过的参照配置完全一致——换新模型时以此类结果作为可信度参照。

### inspect — 只看 dump 名、dtype/shape 分布与分类预览

```bash
python generate_postprocess.py inspect --dump-json dump.json --matrices-dir matrices
```

每个 module path 列出数量、input/output、以及 dtype/shape 变体（判断同一 path 下
混了哪些空间）。写自定义规则前先跑这个。

## 自定义规则文件格式（rules.yaml）

结构与内置规则一致（见脚本 `DEFAULT_RULES`）：

```yaml
rules:
  - pattern: '^Module\.model\.layers\.(?P<layer>\d+)\.input_layernorm\.\w+\.forward\.\d+\.output\.\d+\.pt$'
    matrix: 'model.layers.{layer}.input_layernorm'   # 相对 matrices-dir 的矩阵名（不带 .npy）
    desc: 'input_layernorm output'
```

- `(?P<layer>\d+)` 捕获组可用 `{layer}` 引用（也支持 `{1}` 位置组）。
- matrix 是绝对路径时直接引用；相对名时查 `<matrices-dir>/<name>.npy`。

## validate_yaml.py 用法

```bash
python validate_yaml.py postprocess.yaml --dump-json /quant_dump/rank0/dump.json
```

检查 schema、矩阵文件存在/方形、映射名都在 dump 里；若本机 msprobe 有
`TensorPostprocessManager` 则同时加载验证。

## 已知坑

- **YAML schema 固定**为 `right_matmul: {target_tensor_map: {矩阵: [名字]}, golden_tensor_map: {}}`
  （经真实可用的原版配置确认）。若比对机 msprobe 版本结构不同（如多包一层
  `tensor_postprocess:`），以那台机器上已验证可用的配置为准，手改顶层 key。
- safetensors 矩阵**静默失败**：msprobe 只认 .npy/.pt，失败仅一行 warning，tensor 原样通过 → 必须先 convert。
- R 非对称：`|R-R^T|` 可达 0.3+，直接右乘 R 是错的且无任何报错。
- dump 的 Module class 名 / input 索引随框架与算子版本漂移，正则别写死 class 名和
  arg 索引，空间判定靠内容门控（dtype/last-dim）；同一 module path 下常混多个空间。
- 纯名字列表（无元数据）时门控失效，覆盖报告少一道保险，尽量直接给 dump.json。
- 数值来源不明的 tensor（warmup/MTP 残留，如与主路径 cos≈0.27 的 logits_processor.input）宁可不配：
  配错矩阵比不配更糟。