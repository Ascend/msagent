# 公共接入约定

## 依赖与导入

在执行模型的 worker 中惰性导入公开 API：

```python
from msprobe.pytorch import PrecisionDebugger
```

开发环境缺少 msprobe 时仍完成接入和脚本，交付时提醒实际 worker 环境补齐依赖并标明未验证。
不改用内部导入路径、不修改 `sys.path`、不自行安装替代版本；其他 API 与配置字段按实际版本核对。

## ID 与日志

将实际请求 ID 或 `request_id ↔ uid` 映射随数据传到 actor micro-batch。
检查 `extra_fields`、TransferQueue、DataProto / TensorDict 转换后的字段和样本顺序；只写入 metadata 不代表训练侧能收到。
多次采样、重试和分段请求保留独立身份，引擎改写 ID 时保存映射。

沿用现有日志；没有时使用两侧独立的 JSONL。每次有效采集需能恢复：

| 信息 | 记录内容 |
| --- | --- |
| 样本身份 | 实际 request / sample ID、来源及必要映射 |
| 计算片段 | phase、stage、调用序号、timestep 或 token 区间、模型分支 |
| 输入布局 | ID 顺序、样本轴、offset / 长度、CFG / pair 展开、padding |
| 权重来源 | rollout 与 actor 的 policy version，必要时区分 adapter |
| 运行位置 | global step、node / replica / pid / rank；可由目录确定的字段不必重复 |
| 产物位置 | MSProbe 本地 step 和真实 dump 路径 |

每个进程独立写日志和 dump，路径须能区分节点与副本。global step、权重版本和 MSProbe 本地 step 分别记录，不能互相替代。

## 生命周期与窗口

- `DUMP_ON` 控制两侧采集。关闭时不导入 / 初始化 debugger、不改输入或随机状态；开启失败须明确报错。
- 在模型 worker 惰性初始化 debugger，L0 传实际模型；复用已有 hook 和 start / stop，避免重复采集。
- 围绕目标前向执行 start → forward → 记录有效采集元数据 → stop → step；异常路径清理 hook，并将不完整产物标出。
- 日志使用 `step()` 前的实际迭代号，结合当前 MSProbe 的 step / rank 筛选确认产物真实写出。
- `DUMP_PHASE=log_prob|update_actor|all` 只筛选 actor。按实际调用区分 old / current / ref、前向、反向及重计算。
- 需要确定性设置时在初始化阶段统一处理，不在首次被采前向中临时重设 seed。

沿当前执行链自行确认 global step 的来源、计数起点及其到达 actor 的方式，复用已有信息，缺失时最小补传。确保两侧窗口指向同一批目标样本；不同含义的计数先建立映射，不能直接比较。

仔细检查实际 step 的类型，使用当前框架 API 解包到非空、唯一的标量，并与环境变量 `MSPROBE_DUMP_GLOBAL_STEP` 的字符串值都无损转为整数后比较。不能使用容器的字符串表示、直接取首项或依赖隐式类型转换；缺失、非法或无法归一的值须报错，不能当作普通未命中。

用两侧实际输入类型验证命中、跳过和异常；首次窗口判断在返回前记录来源、原始类型、解析值、目标值及结果。短跑须确认两侧实际 start / stop 和目标模块非空 dump；任一侧漏采都须定位原因、修复并重新采集，直到取得可配对的两侧产物。无运行条件时注明未验证。每次前向仍受 `DUMP_ON` 与窗口控制，不能命中一次后持续采集。

## 共用采集参数

以下仅用于诊断 wrapper；路径特有配置见 [diffusion](diffusion.md#采集参数) 或 [omni / AR](omni.md#采集参数)。

| 配置 | 诊断设置 |
| --- | --- |
| 采集窗口 | `DUMP_ON=1`、`MSPROBE_DUMP_GLOBAL_STEP` 指向一个实际训练 step |
| 训练前验证 | `trainer.val_before_train=False` |
| actor 每卡 micro-batch | 当前常见为 `actor_rollout_ref.actor.ppo_micro_batch_size_per_gpu=1` |
| 推理请求并发 | 目标引擎实际生效的 `max_num_seqs=1`，不改变 TP |
| 全局 batch / PPO mini-batch | 取满足 DP 整除和算法约束的最小组合，尽量一个 mini-batch、一个 PPO epoch |
| rollout 采样数量 | `rollout.n` 是每个 prompt 的轨迹数；按数据量和 DP 分配确定，不等于请求并发 |
| 图执行 | hook 需要 eager 时设置目标 stage 的 `enforce_eager=True` |

目标是每卡每次 micro-batch 一个逻辑样本，尽量减少更新次数。不要统一把全局 batch、mini-batch 和采样数写成 1，也不要把一次更新等同于一次前向。
按当前 recipe 核对采样 / 偏好对展开、PPO epochs 和 timestep 循环；不能简化时保留合法值并记录布局。
若使用 `data.gen_batch_size`，沿其读取点确认生成数据批次大小；它不替代引擎的 `max_num_seqs`。

沿配置构造核对最终值，尤其是 rollout 字段与 `engine_kwargs.vllm_omni` 的覆盖关系。
物理 batch 仍可能包含 CFG、pair 或多模态展开，需保留切片映射；statistics 不能事后拆出单个样本。
LoRA、cache 和并行设置默认保留；其他诊断简化及其影响按路径 reference 记录。

## 诊断启动脚本

默认生成短 wrapper，保留原入口、模型、数据、设备与并行配置。先确认原脚本最终训练命令以 `"$@"` 接收 override，且中间 `source` 未改写位置参数。
不透传时做最小透传修复；必须保持原脚本不变时才生成诊断副本，并标明原因、原路径与全部新增配置。

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export DUMP_ON=1
export MSPROBE_DUMP_GLOBAL_STEP="${MSPROBE_DUMP_GLOBAL_STEP:-1}"
exec bash "$SCRIPT_DIR/run_original.sh" \
  trainer.val_before_train=False \
  actor_rollout_ref.actor.ppo_micro_batch_size_per_gpu=1 \
  actor_rollout_ref.rollout.max_num_seqs=1
```

替换原脚本名；按最终读取路径调整 override，并补充适用的 eager、batch 和路径特有配置。
脚本显式传递两侧 MSProbe 配置、输出目录；新增环境变量须有代码读取，且能传到 Ray worker、引擎子进程和远端节点。
执行 `bash -n`，通过原脚本的 dry-run 能力或命令捕获确认最终参数，避免为检查透传而意外启动训练。

## 配置与验收

两侧独立配置和输出目录。可约定 `MSPROBE_CONFIG_ACTOR`、`MSPROBE_CONFIG_GENERATE` 与 `DUMP_PATH`，但导出变量本身不会启用采集。
按实际版本的 [MSProbe 配置文档](https://gitcode.com/Ascend/msprobe/blob/master/docs/zh/user_guide/dump/config_json_introduct.md) 生成配置。

- statistics 用于检查采集链路和统计异常；输入逐元素核验或精确切片比对使用 tensor。
- 模块级采集可用 L0，按目标收窄模块 / rank / step；空筛选仅用于受 global step 窗口限制的短运行。
- 两侧目录和目标模块数据均须非空。缺失时先查初始化、窗口、筛选和环境变量传递。
- 展示一对“样本身份 → 计算片段 → dump 路径”，核对输入 shape / dtype / 布局和权重来源。
- 多候选按阶段、分支、时间位置和版本消歧；缺失证据时报告未配对，不取第一条作为结果。

采集关联成立不代表模型精度一致；statistics 相同也不证明张量相等。没有真实产物时只报告静态检查结果。

## 并行布局的处理决策

记录 dump 位于通信前还是通信后、并行组、rank 映射与分片轴，再选择处理方式：

| 数据形态 | 处理 |
| --- | --- |
| 通信后的完整激活 | 核对样本和布局后直接比较 |
| TP 的不重叠分片 | 将完整侧按相同规则切片，或收集同组 tensor 重建 |
| 通信前的局部贡献 | 按实际 collective 拼接或归约；不能仅凭 shape 使用 `torch.cat` |
| 复制张量 / FSDP rank | 去重或按样本身份配对，不能与 TP rank 按编号匹配 |

仅当各分片是同一逻辑张量的不重叠、无额外 padding 切片时，可聚合 statistics：max / min 取极值，mean 按元素数加权，L2 norm 取平方和再开方。
复制值、重叠切片或待归约的局部贡献不适用这些公式；statistics 无法恢复逐元素结果或删除任意 padding。
可用 MSProbe 支持的合并工具或自编后处理，保留原始 dump、映射与转换规则，并校验重建 shape 和数值。
