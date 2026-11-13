import { createServer } from 'node:http';
import { McpServer } from '@modelcontextprotocol/server';
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import { registerFixtureCapabilities } from './capabilities.mjs';

const controlPort = process.argv[2] === undefined ? undefined : Number(process.argv[2]);
if (controlPort !== undefined && (!Number.isInteger(controlPort) || controlPort < 1 || controlPort > 65535)) {
  throw new Error('Expected an explicit loopback control port.');
}

let activeServer;
let activeCapabilities;
const controlServer = controlPort === undefined ? undefined : createServer((request, response) => {
  if (request.method !== 'POST' || request.url?.split('?')[0] !== '/__xaa_fixture/catalog-change') {
    response.writeHead(404).end();
    return;
  }
  if (!activeServer || !activeCapabilities) {
    response.writeHead(503).end();
    return;
  }
  activeCapabilities.enableLateTool();
  activeCapabilities.enableLateResource();
  activeCapabilities.enableLatePrompt();
  void Promise.all([
    activeServer.server.sendToolListChanged(),
    activeServer.server.sendResourceListChanged(),
    activeServer.server.sendPromptListChanged(),
  ]).then(() => response.writeHead(204).end(), (error) => {
    response.writeHead(500, { 'content-type': 'text/plain' }).end(String(error));
  });
});
controlServer?.listen(controlPort, '127.0.0.1');

serveStdio(({ era }) => {
  const server = new McpServer(
    { name: 'XAA TypeScript SDK fixture', version: '1.0.0' },
    { capabilities: {
      tools: { listChanged: true },
      resources: { listChanged: true, subscribe: true },
      prompts: { listChanged: true },
    } },
  );
  activeServer = server;
  activeCapabilities = registerFixtureCapabilities(server, {
    transport: 'stdio',
    protocolVersion: era,
    publishResourceUpdate: (uri) => server.server.sendResourceUpdated({ uri }),
    publishToolListChange: () => {
      server.server.sendToolListChanged();
    },
    publishResourcesListChange: () => server.server.sendResourceListChanged(),
    publishPromptsListChange: () => server.server.sendPromptListChanged(),
  });
  return server;
});
if (controlServer) {
  process.stdin.once('end', () => controlServer.close());
}
