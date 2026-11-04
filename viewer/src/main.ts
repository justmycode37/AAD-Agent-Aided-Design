import { diagnostics, runApp } from "./app";
import { McpTransport } from "./transport/mcp";

const transport = new McpTransport();
await transport.connect();
diagnostics.host = transport.caps;
await runApp(transport, document.getElementById("aiad-root") as HTMLElement);
