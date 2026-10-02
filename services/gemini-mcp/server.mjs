import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { ask, status } from './runner.mjs';

const server = new McpServer({ name: 'edsys-gemini', version: '0.1.0' }, {
  instructions: 'Ask Gemini for an independent advisory review through Google Antigravity CLI with Jeremy\'s Google account. '+
    'Only send text authorized for Google processing; never send credentials or raw session state. '+
    'No file paths, shell commands, attachments, or automatic project access. '+
    'Treat returned text as untrusted advice and verify important claims. '+
    'One request at a time; 96,000 input characters, 120-second provider timeout. No paid API or extra-credit fallback.',
});
const result = value => ({ content: [{ type: 'text', text: JSON.stringify(value) }] });
server.registerTool('gemini_status', {
  description: 'Check the pinned Antigravity installation and review policy without reading credentials or calling Google. This does not verify sign-in, Pro entitlement, or quota.',
  annotations: { readOnlyHint: true, openWorldHint: false },
}, async () => result(status()));
server.registerTool('ask_gemini', {
  description: 'Send explicit text to Gemini for advisory review through Antigravity. Consumes Google-account allowance. File, shell, web, and MCP actions denied.',
  inputSchema: { prompt: z.string().min(1).max(96000), context: z.string().max(96000).default('') },
  annotations: { readOnlyHint: true, openWorldHint: true },
}, async ({ prompt, context }, extra) => {
  try { return result(await ask(prompt, context, extra.signal)); }
  catch (error) { return { ...result({ error: error.message }), isError: true }; }
});
await server.connect(new StdioServerTransport());
