import { completable, ResourceTemplate } from '@modelcontextprotocol/server';
import * as z from 'zod/v4';

export function registerFixtureCapabilities(
  server,
  {
    transport,
    protocolVersion,
    publishResourceUpdate,
    publishToolListChange,
    publishResourcesListChange,
    publishPromptsListChange,
    lateToolEnabled = false,
    lateResourceEnabled = false,
    latePromptEnabled = false,
  },
) {
  let lateToolRegistered = false;
  let lateResourceRegistered = false;
  let latePromptRegistered = false;
  const enableLateTool = () => {
    if (!lateToolRegistered) {
      lateToolRegistered = true;
      registerLateTool(server, transport);
    }
  };
  const enableLateResource = () => {
    if (!lateResourceRegistered) {
      lateResourceRegistered = true;
      registerLateResource(server, transport);
    }
  };
  const enableLatePrompt = () => {
    if (!latePromptRegistered) {
      latePromptRegistered = true;
      registerLatePrompt(server, transport);
    }
  };
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
  if (lateResourceEnabled) enableLateResource();
  server.registerResource(
    'fixture_item',
    new ResourceTemplate('xaa://typescript/items/{item_id}', {
      list: undefined,
      complete: {
        item_id: (value) => ['two words', 'typescript-item'].filter((item) => item.startsWith(value)),
      },
    }),
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
  if (lateToolEnabled) enableLateTool();
  server.registerTool(
    'publish_tool_list_change',
    {
      description: 'Add a read-only tool and publish a TypeScript fixture tool-list change.',
      inputSchema: z.object({}),
    },
    async () => {
      enableLateTool();
      await publishToolListChange();
      return { content: [{ type: 'text', text: `typescript-${transport}-tool-list-change-published` }] };
    },
  );
  server.registerTool(
    'publish_resources_list_change',
    {
      description: 'Add a resource and publish a TypeScript fixture resource-list change.',
      inputSchema: z.object({}),
    },
    async () => {
      enableLateResource();
      await publishResourcesListChange();
      return { content: [{ type: 'text', text: `typescript-${transport}-resources-list-change-published` }] };
    },
  );
  server.registerPrompt(
    'summarize_topic',
    {
      title: 'Summarize a topic',
      description: 'Return a deterministic text-only TypeScript fixture prompt.',
      argsSchema: {
        topic: completable(
          z.string().min(1).max(128),
          (value) => ['summary', 'summarize', 'topic'].filter((item) => item.startsWith(value)),
        ),
      },
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
  if (latePromptEnabled) enableLatePrompt();
  server.registerTool(
    'publish_prompts_list_change',
    {
      description: 'Add a prompt and publish a TypeScript fixture prompt-list change.',
      inputSchema: z.object({}),
    },
    async () => {
      enableLatePrompt();
      await publishPromptsListChange();
      return { content: [{ type: 'text', text: `typescript-${transport}-prompts-list-change-published` }] };
    },
  );
  return { enableLateTool, enableLateResource, enableLatePrompt };
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

function registerLateResource(server, transport) {
  server.registerResource(
    'late_fixture_resource',
    'xaa://typescript/late',
    {
      title: 'Late fixture resource',
      description: 'Added after connection when the resource catalog changes.',
      mimeType: 'text/plain',
    },
    async (uri) => ({
      contents: [{ uri: uri.href, mimeType: 'text/plain', text: `typescript-${transport}-late-resource` }],
    }),
  );
}

function registerLatePrompt(server, transport) {
  server.registerPrompt(
    'late_fixture_prompt',
    {
      title: 'Late fixture prompt',
      description: 'Added after connection when the prompt catalog changes.',
      argsSchema: { topic: z.string().min(1).max(128) },
    },
    async ({ topic }) => ({
      messages: [{ role: 'user', content: { type: 'text', text: `typescript-${transport}-late-prompt:${topic}` } }],
    }),
  );
}
