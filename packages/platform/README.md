# aiad

The `aiad` command: CLI, local daemon and MCP adapter for [AIAD](../../README.md).

## Remote mode

`aiad serve --http` serves MCP over Streamable HTTP at `/mcp`, for clients that cannot reach a local process.

```bash
aiad serve --add-user alice            # prints a token once; only its hash is stored
aiad serve --http --port 8765          # loopback by default
```

- **Authentication:** each request needs `Authorization: Bearer <token>`. The server refuses to start with no users.
- **Isolation:** each user gets a private home under `<AIAD_HOME>/users/<name>` and their own daemon, so projects, scenes and config never mix. This is not OS-user separation: all daemons run as the account that started the server, and the worker sandbox is the boundary around model code.
- **Network binding:** a non-loopback `--host` needs `--public-url https://...` with TLS terminated in front of AIAD, and needs builds to run in the OS sandbox. Until the engine reports that, the server refuses.
- **In remote mode:** roots are not requested, `aiad_show` is always inline, and `aiad_import` reads only from the user's own home.
- **Not done:** claude.ai and ChatGPT connectors need OAuth 2.1, which this does not implement. File upload for imports is also not implemented. Neither has been tested with those hosts.
