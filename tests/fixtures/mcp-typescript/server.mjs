import { McpServer } from '@modelcontextprotocol/server';
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import { registerFixtureCapabilities } from './capabilities.mjs';

serveStdio(({ era }) => {
  const server = new McpServer(
    { name: 'XAA TypeScript SDK fixture', version: '1.0.0' },
    { capabilities: { tools: { listChanged: true }, resources: { subscribe: true } } },
  );
  registerFixtureCapabilities(server, {
    transport: 'stdio',
    protocolVersion: era,
    publishResourceUpdate: (uri) => server.server.sendResourceUpdated({ uri }),
    publishToolListChange: () => {
      server.server.sendToolListChanged();
    },
  });
  return server;
});
