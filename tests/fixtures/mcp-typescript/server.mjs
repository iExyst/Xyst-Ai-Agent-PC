import { McpServer } from '@modelcontextprotocol/server';
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import * as z from 'zod/v4';

serveStdio(() => {
  const server = new McpServer({ name: 'XAA TypeScript SDK fixture', version: '1.0.0' });
  server.registerTool(
    'echo_value',
    {
      description: 'Return the supplied value with a TypeScript SDK marker.',
      inputSchema: { value: z.string().min(1).max(128) },
    },
    async ({ value }) => ({
      content: [{ type: 'text', text: `typescript-sdk:${value}` }],
    }),
  );
  return server;
});
