<h1 align="center" >MindStudio Agent</h1>

<div align="center">
<p><b><span style="font-size:20px;">One-Stop Debugging and Tuning for Ascend NPU Scenarios</span></b></p>

[![快速入门](https://badgen.net/badge/快速入门/QuickStart/green)](#-getting-started)
[![精确搜索](https://badgen.net/badge/精确搜索/ReadTheDocs/blue)](https://mindstudio-agent.readthedocs.io/zh-cn/latest/)
[![安装指南](https://badgen.net/badge/安装指南/Install/orange)](#-installation-guide)
[![配置文档](https://badgen.net/badge/配置文档/Docs/purple)](docs/en/user_guide/configuration-and-extension.md)
[![昇腾社区](https://badgen.net/badge/昇腾社区/Community/red)](https://www.hiascend.com/cn/developer/software/mindstudio)
[![报告问题](https://badgen.net/badge/报告问题/Issues/cyan)](https://gitcode.com/Ascend/msagent/issues)

</div>

English | [简体中文](./README.md)

## ✨ What's New

<span style="font-size:14px;">

🔹 **[Jun 4, 2026]**: `Hermes` has been renamed to `Profiler`, `Zephyr` to `Quantizer`, and `Icarus` to `Operator`.<br>
🔹 **[May 21, 2026]**: `v26.0.0` released, adding the `Icarus` agent to cover operator performance tuning scenarios.<br>
🔹 **[Apr 27, 2026]**: `v26.0.0.alpha1` released, adding the `Accuracy`/`Zephyr` agents to cover accuracy tuning and model quantization scenarios.<br>
🔹 **[Apr 8, 2026]**: `v0.1.3` released, completing the Deep Agents refactoring and enhancing the `Hermes`/`Minos` agents.<br>
🔹 **[Mar 19, 2026]**: `mindstudio-agent` has been released to PyPI. Install it with `pip install mindstudio-agent`.

</span>

## ℹ️ Introduction

MindStudio-Agent (referred to as `msagent`) is an AI agent workbench for Ascend NPU development, debugging, and tuning scenarios. It combines the CLI, multi-model providers, MCP tools, built-in Skills, and domain agents to help users locate issues faster and form actionable suggestions in tasks such as performance tuning, accuracy analysis, model quantization, operator optimization, documentation experience, and code review.

<p align="center">
  <img src="docs/en/figures/best_practices/msagent-hello.gif" alt="msAgent" width="720">
</p>

## ⚙️ Features

| Name | Core Capability |
|---|---|
| [**Profiler**](docs/en/agent_guide/Profiler.md) | **[Performance tuning]** Focuses on Ascend Profiling analysis, covering single-device, multi-device, and cluster scenarios. It excels at locating performance issues such as fast/slow devices, slow nodes, MFU, communication bottlenecks, operator hotspots, and launch scheduling, and provides optimization suggestions. |
| [**Accuracy**](docs/en/agent_guide/Accuracy.md) | **[Accuracy tuning]** Focuses on Ascend accuracy analysis and optimization, covering common accuracy issues such as RL training-inference consistency analysis and loss/gnorm NaN analysis. |
| [**Quantizer**](docs/en/agent_guide/Quantizer.md) | **[Model quantization]** Focuses on msModelSlim quantization and compression scenarios, assisting with model adaptation feasibility, structural risk assessment, and basic adapter development. |
| [**Modeling**](docs/en/agent_guide/Modeling.md) | **[Simulation and modeling]** Focuses on simulation and modeling scenarios for large models (LLM/VLM), handling issues such as performance modeling, single-point simulation, throughput planning, device profiling, and model integration preparation. |
| [**Operator**](docs/en/agent_guide/Operator.md) | **[Operator tuning]** Focuses on Ascend NPU operator performance tuning, including in-depth operator performance analysis and end-to-end operator performance optimization, helping improve tuning efficiency and reduce development complexity. |
| [**Minos**](docs/en/agent_guide/Minos.md) | **[Documentation experience and code review]** Focuses on README walkthrough, installation process verification, Quick Start experience, new-user onboarding, documentation usability assessment, and GitCode PR review and review comment organization. |

## 🧩 Skills

In addition to domain agents, `msagent` includes a set of reusable Skills covering scenarios such as Profiling data analysis, operator performance tuning, accuracy overflow detection, documentation experience review, and code review. For the complete Skill list, triggering methods, and dependency descriptions, see [Skills](./skills/README.md).

To integrate Skills or `msprof-mcp` into external agents such as Trae, Claude, and Codex, see the [Integration Guide](./docs/en/user_guide/integration-guide.md).

## 🚀 Getting Started

To quickly experience the core features, see [msAgent Quick Start](docs/en/getting_started/quick_start.md).

## 📦 Installation Guide

For the environment dependencies and installation methods of the tool, see the [msAgent Installation Guide](docs/en/getting_started/install_guide.md).

## 📘 User Guide

For detailed usage instructions, see the [msAgent User Guide](docs/en/user_guide/usemap.md).

## ❓ FAQ

For common issues and troubleshooting entry points, see [FAQ](docs/en/user_guide/faq.md).

## 🌌 Intelligent Search

To make it easier to access information in the documentation, we recommend locating information through the following entry points:

🔹 [Configuration and Extension](docs/en/user_guide/configuration-and-extension.md): Find local configuration directories, MCP configuration, Skills extension, and loading order.<br>
🔹 [Version and Compatibility](docs/en/developer_guide/version-and-compatibility.md): Find version requirements, compatibility policies, and built-in dependencies.<br>
🔹 Ask `msagent` directly in the session: the corresponding agent helps locate issues by combining repository documentation, configuration, and context.

## 🛠️ Contribution Guide

You are welcome to submit Issues, PRs, or new domain Skills. For the complete process, development self-checks, and various contribution guidelines, see the [Contribution Guide](docs/en/developer_guide/contributing.md).

## ⚖️ Related Information

🔹 [Version and Compatibility](docs/en/developer_guide/version-and-compatibility.md)<br>
🔹 [Security Statement](docs/en/legal/SECURITY.md)<br>
🔹 [Disclaimer](docs/en/legal/DISCLAIMER.md)<br>
🔹 [License Notice](docs/en/legal/LICENSE_intro.md)<br>

## 🤝 Suggestions and Communication

You are welcome to contribute to the community. If you have any questions or suggestions, please submit [Issues](https://gitcode.com/Ascend/msagent/issues). We will reply as soon as possible. Thank you for your support.

|                      Live Interaction (WeChat Group)                      |                      Official Updates (Official Account)                      | In-depth Support (Assistant/Forum)                                        |
| :----------------------------------------------------------: | :----------------------------------------------------------: | :----------------------------------------------------------- |
| <img src="https://raw.gitcode.com/mengguangxin/docs/files/dev_0526/common/Writing_Template/figures/qr_code_wechat_work.png" width="120"><br><sub>*Scan the QR code to join the technical discussion group*</sub> | <img src="https://raw.gitcode.com/mengguangxin/docs/files/dev_0526/common/Writing_Template/figures/qr_code_wechat_official_account.png" width="120"><br><sub>*Scan the QR code to follow the official account*</sub> | Scan the QR codes to join the group and follow the official account, providing the fastest way to connect with MindStudio users and developers:<br> **Ask questions quickly:** Discuss technical issues with community members in real time<br>**Stay up to date:** Get notifications about version releases and feature updates as soon as they are available<br> **Share experience:** Exchange best practices and hands-on experience with developers  <br> <br> **More support channels**: 👉 Ascend Assistant: [![WeChat](https://img.shields.io/badge/WeChat-07C160?style=flat-square&logo=wechat&logoColor=white)](https://gitcode.com/Ascend/msit/blob/master/docs/zh/figures/readme/xiaozhushou.png) 👉 Ascend Forum: [![Website](https://img.shields.io/badge/Website-%231e37ff?style=flat-square&logo=RSS&logoColor=white)](https://www.hiascend.com/forum/) |

## 🙏 Acknowledgments

This tool is jointly contributed by the following departments of Huawei:<br>
🔹 Ascend Computing MindStudio Development Department<br>
🔹 Ascend Computing Ecosystem Enablement Department

Thank you to everyone in the community for every PR. Contributions are welcome!
