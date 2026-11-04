# AIAD

CAD for agents: an MCP server that lets any agent design real 2D and 3D parts, prove they meet the request, and show them to the user in an interactive viewer in the chat.

Pre-release and not yet published. `PLAN.md` is the scope and `CONTRACTS.md` is the binding set of interfaces.

## Develop

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/) (`brew install uv`).

```bash
uv sync --all-packages              # create .venv with every workspace package
uv run aiad version                 # run the CLI from the workspace
uv run pytest packages/platform     # one package's tests
uv run pytest tests/e2e             # end-to-end smoke test
```

A new package needs only `packages/<name>/pyproject.toml`; the root workspace picks it up. After changing dependencies, run `uv lock` and commit `uv.lock`.

CI runs on macOS Intel (`macos-15-intel`) and Linux. Hosted arm64 jobs exhausted runner capacity (issue #60); local macOS arm64 smoke results remain separate evidence. Every PR runs the smoke test. Documentation skips package tests; platform and viewer changes run their own tests. Shared CAD, root config and CI changes run all package tests, as does `main`.
