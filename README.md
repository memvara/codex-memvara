# codex-memvara

Give Codex (and ChatGPT, once listed) a memory it can prove — hosted MCP
and the skill that says how to use it.

```
codex plugin marketplace add memvara/codex-memvara
```

Then install `memvara` from that marketplace. The first connection opens
a browser so you can click Allow. That grant lasts 90 days, and no API key
ships in the plugin files.

## What runs on your machine, and the one thing you must do

Until 0.2.5 this page said nothing ran in the background. That was true of
every version before this one and is not true now, so the sentence is gone
rather than softened.

The plugin ships hooks. On every prompt Codex runs `python3
hooks/run.py recall`, at session start `session_start`, and when a turn ends
`capture`, which spends 12-14 seconds mining the turn that just finished.
Capture is declared *synchronous* and forks itself into its own process
group, so the turn is never held open — an async hook does not run at all on
this client, measured on codex-cli 0.151.0.

**Codex will not run any of it until you trust the hooks.** This is the part
worth reading twice: an untrusted hook is not refused, it is silently
skipped. Three test runs produced no output at all while Codex was visibly
parsing the file and warning about timeouts in it. Nothing said "blocked".
If memory never appears, that is the first thing to check — Codex records
the decision under `hooks.state` in `~/.codex/config.toml`.

Nothing this plugin prints reaches your screen: a hook's `systemMessage`
reaches neither the model nor the terminal here, measured. Its account of
itself is `~/.memvara/.hooks/` — `hooks.log` for the read path and
`capture.log` for the write path, where every run writes a line including
the runs that decide to do nothing.

Capture shells out to `claude -p` to mine a turn. Without Claude Code
installed, extraction logs that it could not run and raises an alert on the
next prompt rather than storing nothing in silence.

## When the browser sign-in will not finish

The skill ships `skills/memvara/scripts/memvara_auth.py`: the device-code
flow, standard library only, no `pip install`, and nothing left running
when it returns. Ask Codex to authenticate memvara and it runs the script,
which prints a short code and a URL for you to approve and then writes
`~/.memvara/credentials.json`. It also does `logout` and `stats`.

A Codex plugin cannot ship slash commands — `commands` is not a field its
manifest accepts, and `validate_plugin.py` rejects it the same way it
rejects a field that does not exist — so there is no `/memvara
authenticate` here. Asking in words is the interface on this host.

## What you get

Fourteen tools on `https://app.memvara.dev/mcp`, plus the `memvara` skill.

## ChatGPT

Until this plugin is in OpenAI's public directory, ChatGPT still uses
Developer mode and paste `https://app.memvara.dev/mcp`. See
[memvara.dev/docs/agents/chatgpt](https://memvara.dev/docs/agents/chatgpt).
Do not install the Claude Code marketplace into ChatGPT.

ChatGPT desktop can also add this git marketplace
(`.agents/plugins/marketplace.json`, and `.claude-plugin/marketplace.json`
as the legacy-compatible path).

## Teach it your vocabulary

The built-in predicates are a personal-assistant vocabulary. A store of engineering facts
matches none of them, and an unknown predicate takes the safe default twice over:
multi-valued, so nothing supersedes it, and slow-decaying, so this morning's deploy still
ranks as fresh in two years. The first half shows up on the write receipt. The second is
silent.

Server-side configuration, so it is set where the server is launched:

```bash
MEMVARA_PREDICATES=engineering        # or: engineering,./ours.toml
```

A declaration outranks a guess, so a pack corrects a store that already classified
something wrongly rather than only shaping a fresh one.

## Coming from another memory product

```python
from memvara.compat import import_mem0, import_supermemory
```

mem0 records what changed and when, so that import rebuilds supersession. Supermemory
records current state, so its documents arrive as episodes on their original timestamps
and nothing invents a history it was never told — which means plain recall answers from
claims and looks empty until you ask for `include_episodes`. The skill says this at the
point of use.

## Other clients

Claude Code: [memvara/claude-memvara](https://github.com/memvara/claude-memvara).
A loop you wrote is `pip install memvara`.

## License

Apache-2.0. Skill vendored from [memvara/memvara](https://github.com/memvara/memvara).
