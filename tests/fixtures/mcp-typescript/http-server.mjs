import { createServer } from 'node:http';
import { createMcpHandler, McpServer } from '@modelcontextprotocol/server';
import {
  localhostHostValidation,
  localhostOriginValidation,
  toNodeHandler,
} from '@modelcontextprotocol/node';
import { registerFixtureCapabilities } from './capabilities.mjs';

const port = Number(process.argv[2]);
if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error('Expected an explicit loopback port.');
}

let handler;
let lateToolEnabled = false;
let lateResourceEnabled = false;
let latePromptEnabled = false;
handler = createMcpHandler(({ era, requestInfo }) => {
  const server = new McpServer(
    { name: 'XAA TypeScript HTTP fixture', version: '1.0.0' },
    { capabilities: {
      tools: { listChanged: true },
      resources: { listChanged: true, subscribe: true },
      prompts: { listChanged: true },
    } },
  );
  const protocolVersion = requestInfo.headers.get('mcp-protocol-version') ?? era;
  registerFixtureCapabilities(server, {
    transport: 'http',
    protocolVersion,
    publishResourceUpdate: (uri) => handler.notify.resourceUpdated(uri),
    lateToolEnabled,
    lateResourceEnabled,
    latePromptEnabled,
    publishToolListChange: () => {
      lateToolEnabled = true;
      handler.notify.toolsChanged();
    },
    publishResourcesListChange: () => {
      lateResourceEnabled = true;
      handler.notify.resourcesChanged();
    },
    publishPromptsListChange: () => {
      latePromptEnabled = true;
      handler.notify.promptsChanged();
    },
  });
  return server;
});
const nodeHandler = toNodeHandler(handler);
const validateHost = localhostHostValidation();
const validateOrigin = localhostOriginValidation();
const httpServer = createServer((request, response) => {
  if (!validateHost(request, response) || !validateOrigin(request, response)) return;
  if (request.url?.split('?')[0] !== '/mcp') {
    response.writeHead(404).end();
    return;
  }
  void nodeHandler(request, response);
});

httpServer.listen(port, '127.0.0.1');

async function shutdown() {
  await handler.close();
  httpServer.close(() => process.exit(0));
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
