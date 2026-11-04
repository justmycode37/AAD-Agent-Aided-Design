# Host matrix

What happens when a real host runs AIAD. "Tools" means the real flow ran: `aiad_new`, `aiad_build` on a real build123d part, `aiad_show`. "Viewer" means a person or a browser saw the part. A row stays unconfirmed until there is evidence.

Last run 2026-11-04, macOS arm64, with the M0 stub viewer and stub tessellator. S3's viewer has not been tested on any host through this adapter yet.

| Host | Tools | `supports_ui` seen | Viewer | Evidence |
|---|---|---|---|---|
| MCPJam 2.0.9 | Pass (`aiad_show`) | true | **Inline renders.** WebGL, chunked load and sha256 check all ran | [screenshot](host-matrix/mcpjam-2.0.9-inline-stub.png) |
| T3 + Codex | Pass | true (`codex-mcp-client/0.166.0`) | Unconfirmed. T3 captured the app, but no chunk read has arrived | Result has `t3McpApp` with `resourceUri: ui://aiad/viewer`, and the HTML is in T3's attachments |
| T3 + Claude | Pass | false (`claude-code/2.1.318`) | Not inline, as expected from `PLAN.md`. Window path not tested on this host | Daemon log |
| Codex CLI (`codex exec`) | Pass, with the approval setting below | false (`codex-mcp-client/0.166.0-alpha.5.1`) | Not inline. Window path not tested on this host | Daemon log and `--json` output |
| Claude Code CLI | Not run | | | `claude` is not installed here. T3 + Claude runs the same engine |
| Claude Desktop | Not run | | | |
| Cursor | Not run | | | Not installed here |
| VS Code Copilot | Not run | | | |
| Goose | Not run | | | Not installed here |

## What the runs showed

- **`viewer=auto`:** `aiad_show` shows inline when the host advertises `io.modelcontextprotocol/ui`, otherwise a window. Today's logs support that rule. It is not settled until every row has run.
- **`codex exec`:** with `approval_policy=never`, Codex refuses MCP tools with "MCP tool call requires approval, but approval policy is never". Setting `default_tools_approval_mode = "approve"` on the server fixes it. `aiad install` does not set this for the user, because it would remove the host's prompt for a tool that runs model code. `codex exec` also waits on stdin unless it is closed.
- **MCPJam versions:** 3.18.0 forces an account sign-in. 2.48.1 stalled when its hosted guest-session service returned 503. 2.0.9 works without an account. It ignores `--port` and tries to open a browser, so run it with a `PATH` where `open` does nothing, and drive it headless.
- **MCPJam delivery:** the stub viewer loaded the scene twice for one `aiad_show`, so a viewer must tolerate a repeated tool result.

## Reproducing the MCPJam run

```bash
mkdir -p /tmp/nobrowser && printf '#!/bin/sh\nexit 0\n' > /tmp/nobrowser/open && chmod +x /tmp/nobrowser/open
PATH=/tmp/nobrowser:$PATH npx -y @mcpjam/inspector@2.0.9 --no-open --config mcp.json --server aiad
```

Then open `http://127.0.0.1:6274/#app-builder` in headless Chromium, select `aiad_show`, enter the project and press Run.
