# codex-memvara

Give Codex (and ChatGPT, once listed) a memory it can prove — hosted MCP
and the skill that says how to use it.

```
codex plugin marketplace add memvara/codex-memvara
```

Then install `memvara` from that marketplace. The first connection opens
a browser so you can click Allow. That grant lasts 90 days. Nothing is
installed on the machine: there is no local Python process and we do
not use an API key.

## What you get

Ten tools on `https://app.memvara.dev/mcp`, plus the `memvara` skill.

## ChatGPT

Until this plugin is in OpenAI's public directory, ChatGPT still uses
Developer mode and paste `https://app.memvara.dev/mcp`. See
[memvara.dev/docs/agents/chatgpt](https://memvara.dev/docs/agents/chatgpt).
Do not install the Claude Code marketplace into ChatGPT.

ChatGPT desktop can also add this git marketplace
(`.agents/plugins/marketplace.json`, and `.claude-plugin/marketplace.json`
as the legacy-compatible path).

## Other clients

Claude Code: [memvara/claude-memvara](https://github.com/memvara/claude-memvara).
A loop you wrote is `pip install memvara`.

## License

Apache-2.0. Skill vendored from [memvara/memvara](https://github.com/memvara/memvara).
