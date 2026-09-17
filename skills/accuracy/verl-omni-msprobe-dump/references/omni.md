# Omni / AR 打点指导

用于指定 AR stage 的 prefill 采集；diffusion stage 读 [diffusion.md](diffusion.md)。
参考 [MSProbe verl V1 采集指导](https://gitcode.com/Ascend/msprobe/blob/master/docs/zh/user_guide/dump/verl_v1_trainer_consistency_preprocess_dump.md)，按当前 verl-omni 的配置、worker 和数据结构适配。

## 定位前向

| 目标 | 搜索起点 | 确认内容 |
| --- | --- | --- |
| actor | `main_omni`、`OmniFSDPEngine`、继承的 verl engine | 实际 micro-batch / `forward_step` 的模型调用 |
| ID 流 | `LLMServerClient`、agent loop、`extra_fields`、TQ | 实际请求 ID 如何到达 actor |
| rollout 配置 | `_get_engine_kwargs_key`、`OmniEngineArgs`、stage 配置 | 参数最终到达哪个 worker |
| rollout 前向 | model runner、`execute_model`、`PrecisionDebugger` | hook 包围的模型及调度 token 范围 |

确认实际加载的依赖路径，并沿继承和实例化链追到模型调用；`OmniFSDPEngine` 可能仅补充输入。
采用 stepwise 执行时继续检查相应 worker 入口与 callback，不能只包裹整段 pipeline。

## 推理侧接入

优先复用目标 stage 的 MSProbe 集成；否则在持有模型的 worker / model runner 接入，不在 HTTP client、`ARStrategy` 或请求调度层代替打点。

上游可能使用 `additional_config.dump_config_path`，当前路径可能经 `engine_kwargs.vllm_omni` 和 stage 参数转换。
沿读取点确认配置、eager 条件及调度日志确实生效，不只替换 key。

记录实际请求、stage、prefill / decode、token 范围及样本布局。
chunked prefill、prefix cache 和混合调度可能只计算部分 prompt；按实际区间配对。

## ID 与训练输入

上游 V1 的 ID 传递线索：

```text
LLMServerClient → TokenOutput.extra_fields → AgentLoopOutput.extra_fields
→ AgentLoopWorkerTQ 顶层字段 → TransferQueue → actor TensorDict → micro-batch
```

按当前实现验证整条链路；多 stage、续跑和多轮请求保留各段身份。首段 prompt 与包含已生成 token 的后续请求不是同一输入。

prompt-only 诊断在 actor 消费前裁剪 batch 副本，不修改队列原数据：

| 输入结构 | 裁剪要求 |
| --- | --- |
| jagged TensorDict | 使用每条真实 prompt 长度，同步处理 position IDs 和 response 相关字段 |
| padded DataProto | 按 padding、mask 和有效长度处理，不照搬 jagged 规则 |
| 多模态输入 | 同步核对 embedding、media mask、位置编码及 token 展开，不能只靠文本长度 |

裁剪后须满足 forward / loss 的输入契约。该模式的 loss / 梯度不代表正常训练，不用于 diffusion 或默认 decode 对比。

## 采集参数

公共参数见 [共用采集参数](implementation.md#共用采集参数)。以下保留 MSProbe prompt-only 方案的目标，仅覆盖当前路径实际支持的字段：

| 配置 | 适用要求 |
| --- | --- |
| `DUMP_PHASE=update_actor`、`PROMPTS_ONLY=1` | 仅用于 actor prompt-only 方案；是接入约定，须有代码读取，阶段筛选不关闭 rollout |
| `actor_rollout_ref.model.use_remove_padding=True` | 按训练后端支持情况启用，保留有效 token 的 offset |
| `actor_rollout_ref.actor.use_dynamic_bsz=False` | 关闭动态组批，稳定训练侧 token 布局 |
| `trainer.balance_batch=False` | 关闭跨 rank 样本重排 |
| chunked prefill / prefix cache 等 | 需要完整 prompt 前向时关闭会拆段或跳过计算的功能；保留时按实际 token 区间配对 |
| 多 stage 配置 | 仅覆盖本次目标 stage，验证其实际 `max_num_seqs` 和采集配置 |

不支持或不适用的项简要说明原因，并记录实际 padding、样本顺序与 token 范围。
缩短生成轮数或响应长度时保留 prompt 和 tokenizer 设置。

## 配对验证

选择一个实际请求、一个 stage 的一段 prefill，对应 actor 的相同 token / 多模态输入。
核对权重版本、目标模块、输入和布局后，用日志定位两侧 dump；多轮、多 stage 或重复训练按计算片段消歧。
产物非空和采集窗口验收见 [公共接入约定](implementation.md#配置与验收)。
