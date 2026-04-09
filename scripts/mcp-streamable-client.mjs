/**
 * Minimal Streamable HTTP MCP client for smoke scripts (Node 18+).
 * Not a general SDK — assumes JSON-RPC over SSE responses from POST /mcp.
 */

export const MCP_ACCEPT = 'application/json, text/event-stream';

export function parseSseJsonMessages(text) {
  const messages = [];
  for (const block of text.split(/\r?\n\r?\n/)) {
    for (const line of block.split(/\r?\n/)) {
      if (!line.startsWith('data:')) continue;
      const raw = line.replace(/^data:\s?/, '').trim();
      if (!raw) continue;
      try {
        messages.push(JSON.parse(raw));
      } catch {
        /* ignore */
      }
    }
  }
  return messages;
}

export function mcpSessionId(headers) {
  if (!headers || typeof headers.get !== 'function') return '';
  return (
    headers.get('mcp-session-id') ||
    headers.get('Mcp-Session-Id') ||
    ''
  ).trim();
}

export function authHeaders(bearer) {
  const h = {
    'Content-Type': 'application/json',
    Accept: MCP_ACCEPT,
  };
  if (bearer) h.Authorization = `Bearer ${bearer}`;
  return h;
}

export function resolveMcpBearer(flags) {
  if (flags?.bearer && typeof flags.bearer === 'string') return flags.bearer.trim();
  for (const v of [
    process.env.EDA_SMOKE_ACCESS_TOKEN,
    process.env.EDA_INTEGRATION_COGNITO_ACCESS_TOKEN,
    process.env.EDA_API_KEY,
    process.env.EDA_SMOKE_API_KEY,
    process.env.MCP_SERVICE_TOKEN,
  ]) {
    if (v && String(v).trim()) return String(v).trim();
  }
  return '';
}

/** Extract tool result payload (structured JSON or parsed text content). */
export function unwrapToolResult(result) {
  if (!result || typeof result !== 'object') return result;
  if (result.isError === true) {
    const bits = result.content || result.structuredContent || result;
    const msg = typeof bits === 'string' ? bits : JSON.stringify(bits).slice(0, 1200);
    throw new Error(`MCP tool error: ${msg}`);
  }
  if (result.structuredContent !== undefined && result.structuredContent !== null) {
    return result.structuredContent;
  }
  const text = result.content?.[0]?.text;
  if (text && typeof text === 'string') {
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }
  return result;
}

export async function mcpOpenSession(mcpBase, bearer) {
  const initBody = {
    jsonrpc: '2.0',
    id: 1,
    method: 'initialize',
    params: {
      protocolVersion: '2024-11-05',
      capabilities: {},
      clientInfo: { name: 'mcp-streamable-client', version: '0' },
    },
  };
  const initRes = await fetch(`${mcpBase}/mcp`, {
    method: 'POST',
    headers: authHeaders(bearer),
    body: JSON.stringify(initBody),
  });
  const initText = await initRes.text();
  if (initRes.status !== 200) {
    throw new Error(`initialize: HTTP ${initRes.status}\n${initText.slice(0, 600)}`);
  }
  const sessionId = mcpSessionId(initRes.headers);
  if (!sessionId) throw new Error('initialize: missing mcp-session-id header');
  const initMessages = parseSseJsonMessages(initText);
  const ok = initMessages.some((m) => m.result && (m.result.serverInfo || m.result.protocolVersion));
  if (!ok) throw new Error(`initialize: bad SSE\n${initText.slice(0, 600)}`);
  return sessionId;
}

export async function mcpCallTool(mcpBase, bearer, sessionId, rpcId, name, args) {
  const body = {
    jsonrpc: '2.0',
    id: rpcId,
    method: 'tools/call',
    params: { name, arguments: args },
  };
  const res = await fetch(`${mcpBase}/mcp`, {
    method: 'POST',
    headers: {
      ...authHeaders(bearer),
      'Mcp-Session-Id': sessionId,
    },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (res.status !== 200) {
    throw new Error(`tools/call ${name}: HTTP ${res.status}\n${text.slice(0, 600)}`);
  }
  const messages = parseSseJsonMessages(text);
  const callMsg = [...messages]
    .reverse()
    .find((m) => m && (m.result !== undefined || m.error !== undefined));
  if (!callMsg) throw new Error(`tools/call ${name}: no JSON-RPC in SSE\n${text.slice(0, 600)}`);
  if (callMsg.error) {
    throw new Error(`tools/call ${name} RPC: ${JSON.stringify(callMsg.error)}`);
  }
  return unwrapToolResult(callMsg.result);
}

export async function mcpToolNames(mcpBase, bearer, sessionId) {
  const res = await fetch(`${mcpBase}/mcp`, {
    method: 'POST',
    headers: {
      ...authHeaders(bearer),
      'Mcp-Session-Id': sessionId,
    },
    body: JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} }),
  });
  const text = await res.text();
  if (res.status !== 200) throw new Error(`tools/list: HTTP ${res.status}`);
  const messages = parseSseJsonMessages(text);
  const listMsg = messages.find((m) => m.result && Array.isArray(m.result.tools));
  if (!listMsg) throw new Error('tools/list: unexpected response');
  return listMsg.result.tools.map((t) => t.name);
}
