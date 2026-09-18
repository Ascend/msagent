# 安全声明

如果你发现 MindStudio-Agent 相关安全问题，请优先通过项目 Issue 或维护者认可的私有沟通渠道反馈，并提供可复现信息、影响范围和建议修复方式。

在安全问题完成确认和修复前，请避免公开披露可利用细节。

`msagent` 会将用户配置、审批规则、会话状态和部分运行期产物保存在 `MSAGENT_HOME`（默认 `~/.msagent/`）下的本地文件中。这些文件可能包含用户 prompt、工具调用参数、审计记录、trace 内容以及其他运行上下文。

本地存储目录的访问控制与备份保护由用户自行负责。请根据实际安全要求保护 `MSAGENT_HOME` 及其所在主机，不要在 prompt、长期记忆、配置文件或工具参数中写入不必要的 API Key、Token、密码、Bearer 凭据等敏感信息。

## 命令审批与授权风险

`msagent` 支持 Safe Mode 和 Convenience Mode 两种 shell 命令审批模式，可通过 `/permissions` 查看或切换。Safe Mode 下白名单默认批准、黑名单和普通命令逐条确认；Convenience Mode 下普通命令默认批准、黑名单逐条确认。

`approve` / `reject` 仅本次生效；黑名单的 always 规则只保存到会话内，Safe Mode 普通命令的项目级 always 规则会持久化到 `~/.msagent/state/projects/<project-id>/config.approval.json`。用户应谨慎授权删除文件、修改 Git 历史、推送代码、提升权限、安装依赖、执行远程脚本、访问网络或处理敏感数据的命令，并自行承担已授权命令的执行风险。

命令白名单和黑名单仅用于启发式分类，不是安全边界。命令可以通过解释器、脚本、别名、环境变量、命令替换或其他包装方式间接执行操作，因此即使分类结果不是黑名单，用户仍应逐条检查待执行命令；人工确认是最终防线。

当前版本的 `msagent` 不会在用户工作目录下生成 `.msagent/`，也不会为了忽略这些文件而自动修改 `.git/info/exclude` 或 `.gitignore`。如需配置 Git 忽略规则，请由用户自行按需设置。

本仓提供vscode及devcontainer参考配置，便于开发调试，仅限用于安全的调试环境中对本仓进行开发与调试使用，另外请自行检查配置安全性并根据实际需求做调整。
