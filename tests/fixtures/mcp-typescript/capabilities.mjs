import { ResourceTemplate } from '@modelcontextprotocol/server';
import * as z from 'zod/v4';

export function registerFixtureCapabilities(
  server,
  { transport, protocolVersion, publishResourceUpdate, publishToolListChange, lateToolEnabled = false },
) {
  let lateToolRegistered = lateToolEnabled;
  server.registerTool(
    'echo_value',
    {
      description: `Return the supplied value with a TypeScript ${transport} SDK marker.`,
      inputSchema: { value: z.string().min(1).max(128) },
    },
    async ({ value }) => ({
      content: [
        { type: 'text', text: `typescript-${transport}-sdk:${protocolVersion}:${value}` },
      ],
    }),
  );
  server.registerResource(
    'fixture_readme',
    'xaa://typescript/readme',
    {
      title: 'TypeScript fixture readme',
      description: 'A static untrusted resource from the TypeScript SDK fixture.',
      mimeType: 'text/plain',
    },
    async (uri) => ({
      contents: [
        {
          uri: uri.href,
          mimeType: 'text/plain',
          text: `typescript-${transport}-resource:untrusted`,
        },
      ],
    }),
  );
  server.registerResource(
    'fixture_item',
    new ResourceTemplate('xaa://typescript/items/{item_id}', { list: undefined }),
    {
      title: 'TypeScript fixture item',
      description: 'A dynamic text resource from the TypeScript SDK fixture.',
      mimeType: 'text/plain',
    },
    async (uri, { item_id }) => ({
      contents: [
        {
          uri: uri.href,
          mimeType: 'text/plain',
          text: `typescript-${transport}-template:${item_id}`,
        },
      ],
    }),
  );
  server.registerTool(
    'publish_resource_update',
    {
      description: 'Publish a change notification for the TypeScript fixture resource.',
      inputSchema: z.object({}),
    },
    async () => {
      await publishResourceUpdate('xaa://typescript/readme');
      return { content: [{ type: 'text', text: `typescript-${transport}-update-published` }] };
    },
  );
  if (lateToolEnabled) registerLateTool(server, transport);
  server.registerTool(
    'publish_tool_list_change',
    {
      description: 'Add a read-only tool and publish a TypeScript fixture tool-list change.',
      inputSchema: z.object({}),
    },
    async () => {
      if (!lateToolRegistered) {
        lateToolRegistered = true;
        registerLateTool(server, transport);
      }
      await publishToolListChange();
      return { content: [{ type: 'text', text: `typescript-${transport}-tool-list-change-published` }] };
    },
  );
  server.registerPrompt(
    'summarize_topic',
    {
      title: 'Summarize a topic',
      description: 'Return a deterministic text-only TypeScript fixture prompt.',
      argsSchema: { topic: z.string().min(1).max(128) },
    },
    async ({ topic }) => ({
      messages: [
        {
          role: 'user',
          content: {
            type: 'text',
            text: `typescript-${transport}-prompt:${topic}`,
          },
        },
      ],
    }),
  );
}

function registerLateTool(server, transport) {
  server.registerTool(
    'late_fixture_tool',
    {
      description: 'A tool added after connection; it must remain disabled until approved.',
      inputSchema: z.object({}),
    },
    async () => ({ content: [{ type: 'text', text: `typescript-${transport}-late-tool` }] }),
  );
}
