// Run from examples/mcp-node after npm install. Each process owns one agent key.
// Set PACT_MCP_API_KEY in the private process environment, never in tool input.
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';

const key = process.env.PACT_MCP_API_KEY;
if (!key) throw new Error('PACT_MCP_API_KEY is required');

const transport = new StdioClientTransport({
  command: process.env.PACT_PYTHON || 'python',
  args: ['-m', 'app.mcp_server'],
  cwd: process.env.PACT_BACKEND_DIR || '../../backend',
  env: {
    ...process.env,
    PACT_MCP_API_KEY: key,
    PACT_API_URL: process.env.PACT_API_URL || 'http://127.0.0.1:8000',
  },
});
const client = new Client({ name: 'pact-node-example', version: '1.0.0' });
try {
  await client.connect(transport);
  const tools = await client.listTools();
  console.log('PACT tools:', tools.tools.map((tool) => tool.name).join(', '));
  const response = await client.callTool({ name: 'pact_list_contracts', arguments: {} });
  if (response.isError || response.structuredContent?.ok !== true) {
    throw new Error(JSON.stringify(response.structuredContent ?? response.content));
  }
  console.log(JSON.stringify(response.structuredContent.result, null, 2));
} finally {
  await client.close();
}
