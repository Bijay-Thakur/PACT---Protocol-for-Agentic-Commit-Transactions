// Independent Node MCP client against the actual Python stdio facade and a
// disposable authenticated HTTP stub. No real API key, provider, or DB needed.
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';

const here = path.dirname(fileURLToPath(import.meta.url));
const backend = path.resolve(here, '../../backend');
const fakeKey = 'pact_protocol_test_only';
let calls = 0;
const server = http.createServer((req, res) => {
  calls += 1;
  if (req.headers.authorization !== `Bearer ${fakeKey}`) {
    res.writeHead(401, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: { code: 'UNAUTHENTICATED' } }));
    return;
  }
  res.setHeader('content-type', 'application/json');
  if (req.url?.startsWith('/api/v1/contracts')) res.end(JSON.stringify({ contracts: [] }));
  else if (req.url?.startsWith('/api/v1/workflows')) res.end(JSON.stringify({ workflows: [] }));
  else {
    res.writeHead(409);
    res.end(JSON.stringify({ error: { code: 'SPECIFICATION_CLOSED', message: 'stub rejection' } }));
  }
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;

async function session() {
  const transport = new StdioClientTransport({
    command: process.env.PACT_PYTHON || (process.platform === 'win32'
      ? path.resolve(here, '../../.venv/Scripts/python.exe') : 'python'),
    args: ['-m', 'app.mcp_server'], cwd: backend,
    env: {
      PATH: process.env.PATH || '', SystemRoot: process.env.SystemRoot || '',
      PYTHONUNBUFFERED: '1', PACT_MCP_API_KEY: fakeKey,
      PACT_API_URL: `http://127.0.0.1:${port}`,
    },
  });
  const client = new Client({ name: 'pact-protocol-check', version: '1.0.0' });
  await client.connect(transport);
  try {
    const names = (await client.listTools()).tools.map((t) => t.name);
    for (const expected of ['pact_list_workflows', 'pact_list_contracts', 'pact_begin',
      'pact_revise', 'pact_withdraw', 'pact_request_commit']) {
      if (!names.includes(expected)) throw new Error(`missing MCP tool ${expected}`);
    }
    const discovered = await client.callTool({ name: 'pact_list_contracts', arguments: {} });
    if (discovered.structuredContent?.ok !== true) throw new Error('contract discovery failed');
    const workflows = await client.callTool({ name: 'pact_list_workflows', arguments: {} });
    if (workflows.structuredContent?.ok !== true) throw new Error('workflow discovery failed');
    const rejected = await client.callTool({ name: 'pact_revise',
      arguments: { transaction_id: '00000000-0000-0000-0000-000000000000', reason: 'test' } });
    if (rejected.structuredContent?.code !== 'SPECIFICATION_CLOSED') {
      throw new Error('typed rejection lost');
    }
  } finally { await client.close(); }
}

try {
  await session();
  await session();
  if (calls < 6) throw new Error('disconnect/reconnect did not reach the HTTP stub');
  console.log(JSON.stringify({ status: 'PASS', sessions: 2, http_calls: calls,
    boundary: 'independent Node MCP protocol with HTTP stub' }));
} finally { server.close(); }
