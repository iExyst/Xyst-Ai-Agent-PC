import { createServer } from 'node:http';
import { createMcpHandler, McpServer } from '@modelcontextprotocol/server';
import {
  localhostHostValidation,
  localhostOriginValidation,
  toNodeHandler,
} from '@modelcontextprotocol/node';
import * as z from 'zod/v4';

const port = Number(process.argv[2]);
if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error('Expected an explicit loopback port.');
}

const handler = createMcpHandler(({ era }) => {
  const server = new McpServer({ name: 'XAA TypeScript HTTP fixture', version: '1.0.0' });
  server.registerTool(
    'echo_value',
    {
      description: 'Return the supplied value with a TypeScript HTTP SDK marker.',
      inputSchema: { value: z.string().min(1).max(128) },
    },
    async ({ value }) => ({
      content: [{ type: 'text', text: `typescript-http-sdk:${era}:${value}` }],
    }),
  );
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
