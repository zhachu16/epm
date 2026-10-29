# Skills for AI agents

Two [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview)
that teach an agent to work on epm models through the epm MCP connector:

| Skill | Use |
| --- | --- |
| [`epm-create-model`](epm-create-model/SKILL.md) | Build a new model (DCF, LBO, budget, three-statement, ...) as an epm project |
| [`epm-edit-model`](epm-edit-model/SKILL.md) | Inspect, change, extend, fix, run scenarios on, or roll back an existing project |

Each folder holds a `SKILL.md` and the same `reference.md` (tools, operations, definitions,
formulas, diagnostics), so each can be installed on its own.

## The connector

Both skills need the epm MCP server:

```text
https://epm-nm1g.onrender.com/mcp
```

It speaks streamable HTTP and, during the alpha, needs no credentials.

- **Claude (claude.ai, desktop):** Settings → Connectors → **Add custom connector**, with
  that URL.
- **Claude Code:**

```text
claude mcp add --transport http epm https://epm-nm1g.onrender.com/mcp
```

## Installing the skills

- **Claude (claude.ai, desktop):** zip a skill's folder (`epm-create-model/` or
  `epm-edit-model/`) and upload it under Settings → Capabilities → **Skills**.
- **Claude Code:** copy the folders into `~/.claude/skills/` (for you) or into
  `.claude/skills/` of a project (for everyone working on it).
- **Other agents:** give the agent `SKILL.md` and `reference.md` as instructions.

`service/tests/test_skills.py` runs the skills' examples against the service and checks that
every tool and argument they name exists, so the skills stay in step with the connector.
