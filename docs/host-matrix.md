# Host matrix

What happens when a real host runs AAD. "Tools" means the real flow ran: `aad_new`, `aad_build` on a real build123d part, `aad_show`. "Viewer" means a person or a browser saw the part. A row stays unconfirmed until there is evidence.

Last run 2026-11-04, macOS arm64, with the M0 stub viewer and stub tessellator. S3's viewer has not been tested on any host through this adapter yet.

**This table is out of date.** The real viewer has shipped since this run, so every row needs rerunning before it can describe the current viewer.

| Host | Tools | `supports_ui` seen | Viewer | Evidence |
|---|---|---|---|---|
| MCPJam 2.0.9 | Pass (`aad_show`) | true | **Inline renders.** WebGL, chunked load and sha256 check all ran | [screenshot](host-matrix/mcpjam-2.0.9-inline-stub.png) |
| T3 + Codex | Pass | true (`codex-mcp-client/0.166.0`) | Inline scene transfer and interaction confirmed; host screenshot still missing | Real scene-chunk reads arrived after `aad_show`. The user reported the model's horizontal drag behavior and requested the correction merged in #38 |
| T3 + Claude | Pass | false (`claude-code/2.1.318`) | Not inline, as expected. `aad_show` returns a window URL | Daemon log |
| Codex CLI (`codex exec`) | Pass, with the approval setting below | false (`codex-mcp-client/0.166.0-alpha.5.1`) | Not inline. `aad_show` returns a window URL | Daemon log and `--json` output |
| Claude Code CLI 2.1.318 | Pass | false (`claude-code/2.1.318`) | Not inline. `aad_show` returns a window URL | Daemon log, using `--mcp-config` so no config file changed |
| Claude Desktop | Not run | | | |
| Cursor | Not run | | | Not installed here |
| VS Code Copilot | Not run | | | |
| Goose | Not run | | | Not installed here |

## What the runs showed

- **T3 follow-up:** the resumed Codex session created, built and showed a 60 × 40 × 6 mm plate with a 10 mm hole. Its 1,264-byte scene was fetched through `aad_scene_read`. The user requested rounded corners, a reversed horizontal drag direction, and an updated preview in chat. #38 implements those requests. This proves the stub transfer path and observed interaction; it does not provide the missing host screenshot or prove S3's separate viewer.
- **On-demand UI:** the direction in issue #35 is background MCP, with inline CAD display only when explicitly requested. Installation and diagnostics stay in the background.
- **`viewer=auto`:** `aad_show` shows inline when the host advertises `io.modelcontextprotocol/ui`. Elsewhere it returns a local URL and opens nothing. `viewer=window` is the manual opt-in that also opens a browser.
- **Local window:** a URL from `aad_show` renders in headless Chromium, and a new build updated the open page to rev 2 over its WebSocket ([screenshot](host-matrix/local-window-live-update.png)). No real host has been used to open that URL, and no person has opened it. Under `auto` nothing opens by itself: the URL is in the tool result. Only `aad config viewer=window` opens a browser, which follows the user's "no interface for humans" direction in issue #35.
- **`codex exec`:** with `approval_policy=never`, Codex refuses MCP tools with "MCP tool call requires approval, but approval policy is never". Setting `default_tools_approval_mode = "approve"` on the server fixes it. `aad install` does not set this for the user, because it would remove the host's prompt for a tool that runs model code. `codex exec` also waits on stdin unless it is closed.
- **MCPJam versions:** 3.18.0 forces an account sign-in. 2.48.1 stalled when its hosted guest-session service returned 503. 2.0.9 works without an account. It ignores `--port` and tries to open a browser, so run it with a `PATH` where `open` does nothing, and drive it headless.
- **MCPJam delivery:** the stub viewer loaded the scene twice for one `aad_show`, so a viewer must tolerate a repeated tool result.

## Reproducing the MCPJam run

```bash
mkdir -p /tmp/nobrowser && printf '#!/bin/sh\nexit 0\n' > /tmp/nobrowser/open && chmod +x /tmp/nobrowser/open
PATH=/tmp/nobrowser:$PATH npx -y @mcpjam/inspector@2.0.9 --no-open --config mcp.json --server aad
```

Then open `http://127.0.0.1:6274/#app-builder` in headless Chromium, select `aad_show`, enter the project and press Run.
