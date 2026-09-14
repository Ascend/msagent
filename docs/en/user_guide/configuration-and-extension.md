# Configuration and Extension

This document summarizes the project-local configuration, MCP extensions, and Skills extensions in the current `msAgent` code implementation.

## Project-Local Configuration Directory

`msAgent` uses project-local configuration. The first time you run it in a working directory, it automatically copies the default templates from `resources/configs/default/` to:

```text
<working-dir>/.msagent/
```

If the current working directory is a Git repository, the runtime also attempts to add `.msagent/` to `.git/info/exclude` to prevent local configuration from being committed accidentally.

## Default Templates and Runtime Files

The default templates contain the following main content:

| Path | Description |
|---|---|
| .msagent/config.llms.yml | Default model configuration entry file |
| .msagent/llms/*.yml | Additional model alias configurations |
| `.msagent/agents/*.yml` | Agent definitions. By default, they include `Profiler.yml`, `Accuracy.yml`, `Quantizer.yml`, `Modeling.yml`, `Operator.yml`, and `Minos.yml`. |
| .msagent/subagents/*.yml | SubAgent definitions |
| `.msagent/checkpointers/*.yml` | Checkpointer configurations. By default, they include `memory.yml` and `sqlite.yml`. |
| .msagent/sandboxes/*.yml | Sandbox configuration templates |
| .msagent/prompts/ | Prompt templates used by Agents and SubAgents |
| .msagent/skills/ | Built-in Skills distributed with the templates |
| .msagent/config.mcp.json | MCP server configuration |
| .msagent/config.approval.json | Tool approval rule configuration |
| .msagent/README.md | Description of the local configuration directory |

The runtime generates the following files or directories on demand:

| Path | Description |
|---|---|
| .msagent/config.checkpoints.db | Session checkpoint database |
| .msagent/.history | Input history |
| .msagent/memory.md | User preferences and project context memory |
| .msagent/cache/mcp/ | MCP runtime cache |
| .msagent/oauth/mcp/ | MCP OAuth-related cache |
| .msagent/logs/ | Local log directory |
| .msagent/conversation_history/ | Conversation history directory |

## Project Long-Term Memory

`.msagent/memory.md` stores the long-term memory of the current project. Typical content includes user preferences, project background, path notes that remain valid long term, and facts that subsequent tasks need to keep referencing. You can maintain it in an interactive session with the following commands:

| Command | Description |
|---|---|
| `/remember <content>` | Appends a piece of long-term memory. The first use automatically creates `.msagent/memory.md`. |
| `/showmemory` | Displays the long-term memory saved for the current project. |

Later sessions automatically read the valid content from `.msagent/memory.md` and provide it to the Agent as `<user-memory>` context. The default template content is not injected as valid memory.

Long-term memory is suitable for stable information. You are advised not to write sensitive data such as API keys, passwords, or tokens into it. To delete or modify memory, edit `.msagent/memory.md` directly.

## Configuration Loading

The current implementation supports both single-file configuration and directory-based configuration:

- LLM: Reads `.msagent/config.llms.yml` and `.msagent/llms/*.yml`.
- Agent: Reads `.msagent/config.agents.yml` first if it exists, along with `.msagent/agents/*.yml`.
- SubAgent: Reads `.msagent/config.subagents.yml` first if it exists, along with `.msagent/subagents/*.yml`.
- Checkpointer: Reads `.msagent/config.checkpointers.yml` first if it exists, along with `.msagent/checkpointers/*.yml`.
- Sandbox: Reads `.msagent/sandboxes/*.yml`.

The default templates currently use directory-based configuration as the primary approach.

## MCP Configuration

The default templates enable `msprof-mcp`. The current default configuration is equivalent to:

```json
{
  "mcpServers": {
    "msprof-mcp": {
      "command": "msprof-mcp",
      "args": [],
      "transport": "stdio",
      "env": {},
      "include": [],
      "exclude": [],
      "enabled": true,
      "stateful": true,
      "repair_timeout": 30,
      "invoke_timeout": 3600.0
    }
  }
}
```

The common fields are as follows:

| Field | Description |
|---|---|
| command | The command used to start the local MCP service |
| url | The address of the remote MCP service |
| headers | The request headers of the remote MCP service |
| args | The argument list of `command` |
| transport | The transport protocol supports `stdio`, `sse`, `http`, and `websocket`. |
| env | The environment variables injected when the local MCP process starts |
| include/exclude | The tool list to allow or exclude |
| enabled | Whether to enable this MCP service |
| stateful | Whether to keep the connection alive to avoid restarting it on each invocation |
| repair_command | The optional repair command list used when initialization fails |
| repair_timeout | The timeout of the repair command, in seconds |
| timeout | The timeout for establishing a connection |
| sse_read_timeout | The SSE read timeout |
| invoke_timeout | The timeout of a single tool invocation |

Notes:

- `streamable_http`/`streamable-http` is normalized to `http` at runtime.
- When `repair_command` is configured but `repair_timeout` is not explicitly set, it defaults to 30.
- For local `stdio` MCP services such as `msprof-mcp`, pay attention primarily to `stateful` and `invoke_timeout`.

Common usage:

- Use `/mcp` in a session to toggle the enabled state of an existing MCP service.
- Edit `.msagent/config.mcp.json` directly to add, delete, or adjust service definitions.

## Skills Extensions

The current Skills are scanned in the following order:

1. `<working-dir>/skills`
2. Built-in Skills directory: prefers `skills/` in the repository root, and uses the packaged `resources/configs/default/skills/` when a wheel is installed.
3. `<working-dir>/.msagent/skills`

Skills with the same name follow the first-loaded-wins rule. Therefore, the current priority is:

1. `skills/` in the project root directory
2. Built-in Skills
3. `.msagent/skills/`

## Skill Directory Structure

The following two directory structures are supported:

```text
skills/
  my-skill/
    SKILL.md
```

```text
skills/
  profiling/
    my-skill/
      SKILL.md
```

You are advised to include frontmatter in `SKILL.md` with at least:

```yaml
---
name: my-skill
description: What this skill does
---
```

## Built-in Skills When Running from Source

The built-in Skills are merged directly into the `msagent` main repository. When running from source, `msagent` uses the repository root by default:

```text
skills/
```

When building a wheel, the preceding directory is packaged into:

```text
resources/configs/default/skills/
```

Therefore, running from source and running from an installation share the same Skills content. You no longer need to run `git submodule` synchronization separately.

(custom-skill-guide)=
## Adding a Custom Skill

If you want to add a new Skill to the current project, you are advised to place it directly in the repository root:

```text
skills/
  my-skill/
    SKILL.md
```

A categorized structure is also supported:

```text
skills/
  profiling/
    my-skill/
      SKILL.md
```

In this case:

- `profiling` is the category.
- `my-skill` is the skill name.

### Writing `SKILL.md`

Minimal example:

```md
---
name: my-skill
description: A custom skill used to handle a certain type of fixed task
---

# My Skill

Use this skill when the user raises a request of this type:

- Analyze logs
- Generate reports

Execution requirements:

1. First check whether the input is complete.
2. Read the existing configuration and examples in the project first.
3. Output conclusions, evidence, and suggestions.
```

Notes:

- When `name` is not specified, the directory name is used by default.
- You are advised to fill in `description` so the skill can be identified in `/skills`.
- If you need scripts or templates, you can place them in the skill directory, for example, `scripts/` or `templates/`.

### Making the Agent See the Skill

Creating the file alone is not enough. The current agent must also enable this skill in its configuration.

For example:

```yaml
skills:
  patterns:
    - default:my-skill
  use_catalog: false
```

For a skill with a category:

```yaml
skills:
  patterns:
    - profiling:my-skill
  use_catalog: false
```

The rule format is:

```text
<category>:<name_pattern>
```

For more complete matching semantics, refer to [Agent YAML Tool/Skills Filter Rules](agent-tool-skill-filter-rules.md).

### Verifying Whether the Skill Takes Effect

After starting `msagent`, check it as follows:

```text
/skills
```

Or specify the skill directly:

```text
/skills my-skill
```

If skills share the same name, you are advised to write the full name:

```text
/skills profiling/my-skill
```

(custom-skill-faq)=
## Skill FAQ

### The New Skill Does Not Appear in `/skills`

In most cases, check the following items first:

- Check whether the path is correct and whether the file name is `SKILL.md`.
- Check whether the current agent has the corresponding `skills.patterns` configured.
- Check whether a skill with the same name in a higher-priority directory overrides it.

### The Skill Exists, but the Agent Does Not Use It Automatically

This is usually caused by one of the following reasons:

- The `description` is too generic, making it difficult for the model to determine the trigger scenario.
- The agent's `skills.patterns` does not enable the corresponding skill.
- The current task matches another built-in skill better. Therefore, this skill is not selected.

You are advised to first confirm visibility with `/skills`, then add a more specific `description` and trigger instructions.
