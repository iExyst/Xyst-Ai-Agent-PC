import * as z from 'zod/v4';

export function registerFixtureCapabilities(server, { transport, protocolVersion }) {
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
