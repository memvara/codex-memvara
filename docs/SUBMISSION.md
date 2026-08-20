# Submitting to OpenAI's plugin directory

The git marketplace works without this:

```
codex plugin marketplace add memvara/codex-memvara
```

ChatGPT's **public** directory is a portal review, not a git push. Until it
is approved, ChatGPT still uses Developer mode and paste
`https://app.memvara.dev/mcp`.

## Before you open the portal

1. OpenAI org: **Apps Management** write; verified individual or business identity.
2. Domain check: MCP URL is `https://app.memvara.dev/mcp`, so the default
   challenge is `https://app.memvara.dev/.well-known/openai-apps-challenge`.
   The console Worker serves that path. Put the portal token in:

   ```
   cd dashboard && npx wrangler secret put OPENAI_APPS_CHALLENGE
   ```

   Then deploy the console Worker. The response body must be **only** that
   token (text/plain). Not JSON, not the SPA.
3. Portal: **Create plugin** → **With MCP** → Universal URL
   `https://app.memvara.dev/mcp`. Do not point it at an existing ChatGPT
   custom connector.
4. Scan Tools. Skills can be uploaded from `plugin/skills/memvara/` in this
   repo, or imported if the server later advertises the skills extension.
5. Five positive and three negative test cases. Starter prompts are already
   in `.codex-plugin/plugin.json` `interface.defaultPrompt`.
6. Privacy / terms / site: `https://memvara.dev/privacy`,
   `https://memvara.dev/terms`, `https://memvara.dev`.

After OpenAI approves **and** someone publishes from the portal, flip
`/docs/agents/chatgpt` to lead with the directory and keep paste-URL as
the fallback. Not before.
