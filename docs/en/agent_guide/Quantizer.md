# Quantizer: Model Quantization Tuning

`Quantizer` is an agent for msModelSlim model quantization scenarios. It completes quantization accuracy tuning through an **end-to-end tuning process**: you describe your requirements in natural language, and the agent orchestrates environment checks, model preparation, configuration search, quantization execution, and accuracy evaluation. It searches for a quantization configuration that meets the requirements under the specified accuracy constraints and delivers the quantized weights.

## Agent Positioning

- Performs **quantization accuracy tuning** of msModelSlim for Large Language Models (LLMs).
- You do not need to write a complete YAML by hand. You can start the full process by describing the model path, quantization scheme, device, and accuracy target in natural language.
- If the target model is not yet supported by msModelSlim, the process triggers feasibility analysis and adapter development during the **model preparation** phase, with no need for a separate adaptation task.
- Handles the adaptation of basic Transformers models, as well as the integration and tuning of complex models such as MoE packed weight decomposition and layer-by-layer loading of very large models.

## Core Capabilities

- **Model adaptation**: Evaluate the feasibility of integrating the model and complete the development and verification of the msModelSlim adapter. If the model is not yet registered, the adaptation is triggered in the model preparation phase of the tuning process.
- **Accuracy tuning**: Generate quantization and evaluation configurations according to the accuracy constraints you specify, run quantization and AISBench evaluation, and iteratively search for a quantization scheme that meets the requirements.

## Prerequisites

- Prepare an inference runtime environment. You are advised to use the `vllm-ascend` image. For guidance on installing `vllm-ascend` with Docker, see [Installing vllm-ascend](https://docs.vllm.ai/projects/ascend/en/latest/installation.html#set-up-using-docker). You are advised to install msagent in the container and use Quantizer.
- Install a `transformers` version appropriate for your model. Special note: if the `transformers` version required by msModelSlim model quantization differs from the version required by the inference engine for serving, you can provide the relevant information to the agent and let it handle the corresponding version on its own.
- Quantization tuning requires installing msModelSlim in the container. For installation guidance, see [Installing msModelSlim](https://gitcode.com/Ascend/msmodelslim/blob/master/docs/en/install_guide/install_guide.md#23-%E6%BA%90%E7%A0%81%E5%AE%89%E8%A3%85).
- Tuning evaluation depends on the AISBench evaluation service. See its [README](https://github.com/AISBench/benchmark/blob/main/README.md) for installation and usage instructions. You must prepare the datasets required for evaluation (such as gpqa and aime25) yourself. You can refer to the [AISBench dataset preparation guide](https://yh-ais-bench-benchmark.readthedocs.io/en/latest/base_tutorials/all_params/datasets.html).

## Recommended Usage

Just describe your quantization tuning requirements in natural language. The agent extracts the parameters, echoes them for confirmation, and then advances through the full process step by step.

**Key Information to Provide**

| Information | Description |
|------|------|
| Model path | Local directory or HuggingFace repository name |
| Save path | Output directory for the quantized artifacts and process output |
| Quantization scheme | For example, W8A8. If not specified, the agent proposes a default scheme and confirms it with you |
| Device | NPU, CUDA, or CPU, and the device number |
| Accuracy requirements | Relative tolerance (for example, "accuracy loss must not exceed 2%") or an absolute target (for example, "gsm8k must be at least 83%") |
| `trust_remote_code` | Must be confirmed when using a HuggingFace model with custom code. |

**Example Prompts:**

```
Please quantize path/to/Qwen3-32B with W8A8 using NPU 0, and keep the gsm8k accuracy loss within 1% of the baseline
```

```
{Model path} is a new model. Please complete W8A8 quantization tuning for it, keeping the accuracy loss on the gpqa dataset within 1%
```

## End-to-End Tuning Process

Quantizer orchestrates the following phases (each phase proceeds to the next only after you confirm it):

1. **User input alignment**: Extract and echo the key parameters (model path, save path, quantization scheme, device, accuracy requirements, and so on).
2. **Environment preparation**: Confirm that msModelSlim can be imported and that the Ascend environment variables and device numbers are ready.
3. **Model preparation**: Check whether the target model is registered in `config.ini`. **If it is not registered, the process enters the model adaptation subprocess** (see the next section). If it is registered, this phase is skipped.
4. **Quantization configuration tuning**: Loop through "generate quantization configuration → quantize → evaluate → record history" until the accuracy target is met or the maximum number of iterations is reached.
5. **Result delivery**: Output the quantized weights that meet the accuracy requirements, the evaluation report, and the tuning history.

Each phase is handled by a dedicated subagent:

| Subagent | Phase | Responsibility |
|--------|----------|------|
| `msmodelslim-model-analysis` | Model preparation | Pre-adaptation analysis: risk assessment of implementation source, structure, MoE, and layer-by-layer loading |
| `msmodelslim-model-adapt` | Model preparation | After the analysis passes: adaptation templates, registration, `config.ini`, and four-step verification |
| `quant-tuning-evaluation-generator` | Quantization configuration tuning | Generate the evaluation configuration (Evaluation YAML). |
| `quant-tuning-practice-generator` | Quantization configuration tuning | Generate or adjust the quantization configuration (Practice YAML). |
| `quant-tuning-quantizer` | Quantization configuration tuning | Run model quantization according to the Practice YAML. |
| `quant-tuning-evaluator` | Quantization configuration tuning | Run AISBench accuracy evaluation on the quantized model. |

### Model Preparation (Model Adaptation Subprocess)

When the target `model_type` is not yet registered in the msModelSlim `config.ini`, the agent delegates to the analysis and adaptation subagents, and then proceeds to quantization configuration tuning after the adaptation completes.

This subprocess mainly completes the following:

- Evaluate the model implementation source, structural characteristics, and feasibility risks of quantization integration.
- Create a Transformers-based adapter for Decoder-only LLMs.
- Provide solutions such as layer-by-layer loading (lazy loading) for very large models to avoid memory bottlenecks.
- Strictly follow the gate rules and the multi-step verification process to ensure that conclusions are supported by actual evidence (configurations, logs, and command output).

## Usage Notes

- The current model adaptation subprocess mainly supports linear layer quantization such as W8A8 for LLMs. Complex algorithms such as outlier suppression and FA3 are not yet supported.
- If the model analysis phase identifies risk points that are difficult to adapt, the process stops and informs you in advance. Only after you confirm the risks and agree to continue does it proceed to adaptation and subsequent tuning.
- If you do not provide floating-point baseline accuracy, the agent first runs evaluation on the floating-point model to obtain the baseline, and then enters the tuning loop.
