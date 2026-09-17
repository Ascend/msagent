# Diffusion 打点指导

用于 rollout 去噪模型与 actor 对应前向的采集。公共 API、窗口和日志见 [implementation.md](implementation.md)。

## 定位前向

| 目标 | 搜索起点 | 确认内容 |
| --- | --- | --- |
| 入口与数据流 | `main_diffusion_v1`、`trainer/diffusion/v1`、`tq_utils` | ID 与输入如何进入训练 batch |
| 请求提交 | `single_turn_agent_loop.py`、`_get_routing_request_id` | 路由 ID 与模型 worker 实际 ID 的关系 |
| rollout | `pipelines/<recipe>/vllm_omni_rollout_adapter.py` | 去噪循环中的 transformer / DiT 调用 |
| actor | `diffusers_impl.py`、`forward_step`、`prepare_model_inputs` | micro-batch、timestep 与 adapter 前向 |

```bash
rg -n 'trajectory_timesteps|all_timesteps|train_timesteps|request_id|uid' verl_omni
rg -n 'forward_step|predict_noise|denoise_step|step_execution|super\(\)\.forward' verl_omni/pipelines verl_omni/workers
```

按实际 recipe 追踪继承和调用链。`forward()` 与 `denoise_step()` / stepwise model runner 可能走不同路径，打点须覆盖当前执行模式中的一次模型前向。

## 选择可比输入

| 算法 | 训练输入特点 | 采集选择 |
| --- | --- | --- |
| FlowGRPO / DanceGRPO / PPO 类 | 常复用 rollout 的 latent、embedding 和 selected timestep | 追踪轨迹切片到 actor，核对传递、重排、dtype、CFG 和权重版本 |
| NFT 类 | 常从 clean latent、noise 和训练 timestep 重建输入，并执行 old / current / ref 分支 | 保存实际 noise 或 `x_t`、条件与分支；必要时做相同输入的单步重放 |
| DPO 类 | 常独立构造 chosen / rejected pair 的加噪输入 | 记录 pair 成员、noise、时间位置和 adapter，rollout 轨迹不是天然对端 |

以上是搜索线索，以当前输入构造为准。未复用相同输入时说明可比范围；所需重放独立于正常训练。

在 actor micro-batch 检查关联字段和值，不能只检查 rollout 输出。当前部分路径只提取 metadata 的 `prompt_embeddings` / `rl`；新增字段须贯穿实际封装和转换。
TransferQueue 同类记录保持字段集合一致，缺失值不能作为有效 ID；TensorDict 非张量字段用当前接口解包。

### 前处理边界

- embedding / latent 直接复用：验证两侧边界值、shape、dtype、mask 和顺序，通常无需采 encoder 内部。
- actor 重算 text / VAE / 视觉 / 音频 encoder 或 projector：采两侧对应边界输出，发现差异后再扩大模块范围。
- 视频 / 音频输入：记录 media 长度、时间轴、压缩倍率与位置布局。

## 插入打点

**rollout**：模型输入就绪后 start，单次前向结束后记录元数据并 stop / step。请求上下文传到模型 worker，并发时避免共享可覆盖的当前 ID；按实际 dummy 标志过滤 warmup。

**actor**：沿 `_run_forward_backward_batch` / `forward_step` 找实际模型调用，区分 micro-batch、timestep、adapter、old / current / ref 与重计算。

按 [分支收敛](#分支收敛) 验证真实模型入口；CFG 可能分开调用、沿 batch 拼接或分配到不同 rank。双 transformer 或视频 / 音频还需记录实际模型及各自时间轴，不能把模态数当成 guidance 分支数。

## 时间位置与配对

记录轨迹索引、实际 scheduler 时间和模型输入时间；`t`、`sigma`、归一化值按当前 scheduler 规则转换，保留原值。
rollout 循环索引、selected 轨迹索引、actor 调用序号和 MSProbe step 不可混用。

按“样本 / 轨迹 + 时间位置 + 分支 + 权重版本”定位调用。浮点时间按 dtype 和单位设置容差，多候选再用轨迹索引消歧。
核对 latent、条件、mask、输入缩放及布局后，给出两侧日志与 dump 路径。

## 采集参数

公共 batch、并发和窗口限制见 [共用采集参数](implementation.md#共用采集参数)。diffusion 额外注意：

| 设置 | 原则 |
| --- | --- |
| prompt-only | 不使用 `PROMPTS_ONLY`；输入是 latent 与条件 |
| 执行模式 | 保持原 `step_execution` 等模式，沿真实路径打点 |
| 去噪步数 | 缩短会改变时间值与轨迹长度；调整后确认训练 selected timestep 仍有效 |
| guidance / 多分支 | 默认必须收敛为两侧同一个 cond 单分支，按下节验收 |
| LoRA / adapter | 保留原设置，记录实际分支和 wrapper |
| 帧数 / 分辨率 / media 长度 | 调整会改变输入问题；仅用于明确的缩小复现，两侧同步并记录布局 |

### 分支收敛

推理可能将多个 guidance 分支拼成一次前向，训练则逐分支计算，形成 `kB` 对 `B` 的输入布局差异。合并分支的 statistics 不能直接与单分支比较；`max_num_seqs=1` 不能消除这种展开。

**默认诊断必须让两侧都只计算同一个 cond 分支。** 沿当前源码确认关闭 CFG、STG / 扰动和跨模态额外分支的生效参数，写入 wrapper 并同步两侧设置，不能只改通用 `guidance_scale`。在真实模型入口核对分支、调用次数及输入 shape；仍有额外分支则修正后重采。

仅在用户要求保留多分支或模型不支持单分支时，采集 tensor，按实际分支 offset / 调用映射核对相同输入与权重后配对；statistics 不能事后拆分。收敛仅用于本次诊断，会改变采样轨迹，交付需列出覆盖值。
