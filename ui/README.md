# airtight floor

A read-only dashboard for an airtight project: the phase and round, who is taking a turn,
every agent's desk with their last summary and queue, the statements as they accumulate
(and which have concerns), each concern's full thread of answers, hand-offs and replies,
per-agent stats with a who-raises-concerns-to-whom matrix, a turn timeline, and the
`at-*` command log. The page polls every few seconds and redraws when anything changed.

```
ui/setup.sh                                 # builds ui/.venv from pyproject.toml
cd your-project && ~/…/ui/.venv/bin/airtight-ui     # watches .airtight/project.db
airtight-ui --db path/to/project.db --port 8765     # or any database
```

Then open http://127.0.0.1:8765/. Settings live in `airtight-ui.toml` (database, log, plugin
root, host, port, refresh interval); `$AT_DB` and the command-line flags override it.

- **Backend**: FastAPI (`airtight_ui/server.py`). It opens the database read-only on every
  request and reuses the plugin's own `airtight.db` helpers for derived state (the rotation,
  each agent's queue, who must approve), so it shows what the tools would act on.
- **Frontend**: plain HTML, CSS and JavaScript in `airtight_ui/static/`, no build step.
  Links to a detail open in the URL: `/?tab=concerns#concern/7`, `#statement/4`, `#agent/2`.

| Endpoint | Returns |
|---|---|
| `GET /api/snapshot` | everything the page shows, in one call |
| `GET /api/statements/{id}` | one statement with approvals, links, concerns and history |
| `GET /api/concerns/{id}` | one concern's whole thread |
| `GET /api/log?limit=N` | the newest N lines of `log.jsonl` |
| `GET /api/config` | refresh interval and the paths in use |
| `GET /api/docs` | the OpenAPI page |
