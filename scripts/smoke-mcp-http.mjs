#!/usr/bin/env node
/* eslint-env node */
/* global process */
/**
 * EasyDeploy AI — HTTP MCP end-to-end smoke test
 *
 * Exercises Streamable HTTP like validate_mcp_sandbox.sh, then calls tools/call
 * (default: get_account_status) so the MCP server must reach the EasyDeploy REST API.
 *
 * Usage:
 *   node scripts/smoke-mcp-http.mjs
 *   node scripts/smoke-mcp-http.mjs --mcp-base http://127.0.0.1:9000
 *   node scripts/smoke-mcp-http.mjs --bearer eyJhbG...
 *   node scripts/smoke-mcp-http.mjs --tool list_projects --tool-args "{}"
 *
 * Env (same families as scripts/validate_mcp_sandbox.sh):
 *   MCP_SMOKE_BASE_URL          MCP HTTP base (default http://127.0.0.1:8080)
 *   EDA_API_BASE                For optional direct GET …/v1/account
 *   EDA_API_KEY / EDA_SMOKE_API_KEY
 *   EDA_SMOKE_ACCESS_TOKEN / EDA_INTEGRATION_COGNITO_ACCESS_TOKEN  (OAuth /mcp)
 *   MCP_SERVICE_TOKEN           Legacy gate for /mcp when OAuth is off
 *
 * Requires Node 18+ (global fetch).
 */

const RED = '\x1b[31m';
const GREEN = '\x1b[32m';
const CYAN = '\x1b[36m';
const DIM = '\x1b[2m';
const BOLD = '\x1b[1m';
const R = '\x1b[0m';

const ok = (msg) => console.log(`${GREEN}✓${R} ${msg}`);
const info = (msg) => console.log(`${CYAN}ℹ${R} ${msg}`);
const step = (msg) => console.log(`\n${BOLD}${CYAN}▸ ${msg}${R}`);
const fail = (msg) => {
  console.error(`${RED}✗ ${msg}${R}`);
  process.exit(1);
};

const MCP_ACCEPT = 'application/json, text/event-stream';

function parseArgs(argv) {
  const flags = {};
  const positional = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const key = a.slice(2);
      const next = argv[i + 1];
      flags[key] = !next || next.startsWith('--') ? true : (i++, next);
    } else {
      positional.push(a);
    }
  }
  return { flags, positional };
}

function normalizeApiV1Base(raw) {
  if (!raw || typeof raw !== 'string') return '';
  let u = raw.trim();
  while (u.endsWith('/')) u = u.slice(0, -1);
  if (u.endsWith('/v1')) return u;
  return `${u}/v1`;
}

/** Parse SSE body: collect JSON from each `data:` line. */
function parseSseJsonMessages(text) {
  const messages = [];
  const blocks = text.split(/\r?\n\r?\n/);
  for (const block of blocks) {
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

function mcpSessionId(headers) {
  if (!headers || typeof headers.get !== 'function') return '';
  return (
    headers.get('mcp-session-id') ||
    headers.get('Mcp-Session-Id') ||
    ''
  ).trim();
}

function resolveMcpBearer(flags) {
  if (flags.bearer && typeof flags.bearer === 'string') return flags.bearer.trim();
  const fromEnv = [
    process.env.EDA_SMOKE_ACCESS_TOKEN,
    process.env.EDA_INTEGRATION_COGNITO_ACCESS_TOKEN,
    process.env.EDA_API_KEY,
    process.env.EDA_SMOKE_API_KEY,
    process.env.MCP_SERVICE_TOKEN,
  ];
  for (const v of fromEnv) {
    if (v && String(v).trim()) return String(v).trim();
  }
  return '';
}

function authHeaders(bearer) {
  const h = {
    'Content-Type': 'application/json',
    Accept: MCP_ACCEPT,
  };
  if (bearer) h.Authorization = `Bearer ${bearer}`;
  return h;
}

async function httpJson(url, options = {}) {
  const res = await fetch(url, options);
  const text = await res.text();
  let body;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  return { res, text, body };
}

const { flags } = parseArgs(process.argv.slice(2));

const mcpBase = (
  flags['mcp-base'] ||
  process.env.MCP_SMOKE_BASE_URL ||
  'http://127.0.0.1:8080'
)
  .replace(/\/$/, '');

const apiBaseDefault =
  process.env.EDA_API_BASE || 'https://h3h0z4vkf1.execute-api.us-east-1.amazonaws.com/prod';
const apiV1 = normalizeApiV1Base(flags['api-base'] || apiBaseDefault);

const skipDirectApi = flags['skip-direct-api'] === true;
const toolName = typeof flags.tool === 'string' ? flags.tool : 'get_account_status';
let toolArgs = {};
if (typeof flags['tool-args'] === 'string') {
  try {
    toolArgs = JSON.parse(flags['tool-args']);
  } catch (e) {
    fail(`Invalid --tool-args JSON: ${e.message}`);
  }
}

const verbose = flags.verbose === true;
let mcpBearer = resolveMcpBearer(flags);

console.log(`\n${BOLD}${CYAN}EasyDeploy AI — HTTP MCP smoke test${R}`);
info(`MCP base: ${DIM}${mcpBase}${R}`);

// --- 1) healthz
step('GET /healthz');
{
  const { res, body } = await httpJson(`${mcpBase}/healthz`);
  if (res.status !== 200) fail(`healthz: expected 200, got ${res.status}`);
  if (!body || body.status !== 'ok') fail(`healthz: unexpected body ${JSON.stringify(body)}`);
  ok('healthz');
}

// --- 2) OAuth metadata (optional)
step('GET /.well-known/oauth-protected-resource');
let oauthOn = false;
{
  const { res, body } = await httpJson(`${mcpBase}/.well-known/oauth-protected-resource`);
  if (res.status === 404) {
    info('404 — OAuth disabled (legacy mode)');
  } else if (res.status === 200 && body && typeof body === 'object' && body.authorization_servers) {
    oauthOn = true;
    ok(`OAuth metadata (issuer: ${body.authorization_servers[0] || '?'})`);
  } else {
    fail(`oauth metadata: expected 200 or 404, got ${res.status}`);
  }
}

if (oauthOn && !mcpBearer) {
  fail(
    'OAuth is enabled on the MCP server but no bearer token was found. Set one of:\n' +
      '  EDA_SMOKE_ACCESS_TOKEN, EDA_INTEGRATION_COGNITO_ACCESS_TOKEN, EDA_API_KEY, MCP_SERVICE_TOKEN\n' +
      '  or pass --bearer <token>',
  );
}

// --- 3) Direct REST account (optional)
if (!skipDirectApi) {
  const apiKey = process.env.EDA_SMOKE_API_KEY || process.env.EDA_API_KEY || '';
  if (apiKey) {
    step(`GET ${apiV1}/account (direct API, API key)`);
    const { res, text } = await httpJson(`${apiV1}/account`, {
      headers: { Authorization: `Bearer ${apiKey}` },
    });
    if (res.status !== 200) fail(`direct account: ${res.status} ${text.slice(0, 200)}`);
    ok('direct API account');
  } else {
    step('SKIP direct GET /v1/account (set EDA_API_KEY or EDA_SMOKE_API_KEY)');
  }
} else {
  step('SKIP direct API (--skip-direct-api)');
}

// --- 4–6) MCP session
step('MCP initialize');
const initBody = {
  jsonrpc: '2.0',
  id: 1,
  method: 'initialize',
  params: {
    protocolVersion: '2024-11-05',
    capabilities: {},
    clientInfo: { name: 'smoke-mcp-http', version: '0' },
  },
};

const initRes = await fetch(`${mcpBase}/mcp`, {
  method: 'POST',
  headers: authHeaders(mcpBearer),
  body: JSON.stringify(initBody),
});
const initText = await initRes.text();
if (initRes.status !== 200) {
  fail(`initialize: HTTP ${initRes.status}\n${initText.slice(0, 600)}`);
}
const sessionId = mcpSessionId(initRes.headers);
if (!sessionId) fail('initialize: missing mcp-session-id response header');

const initMessages = parseSseJsonMessages(initText);
if (verbose) console.log(DIM + JSON.stringify(initMessages, null, 2) + R);
const initOk = initMessages.some((m) => m.result && (m.result.serverInfo || m.result.protocolVersion));
if (!initOk) fail(`initialize: no successful result in SSE\n${initText.slice(0, 800)}`);
ok(`initialize (session ${sessionId.slice(0, 8)}…)`);

step('MCP tools/list');
const listBody = { jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} };
const listRes = await fetch(`${mcpBase}/mcp`, {
  method: 'POST',
  headers: {
    ...authHeaders(mcpBearer),
    'Mcp-Session-Id': sessionId,
  },
  body: JSON.stringify(listBody),
});
const listText = await listRes.text();
if (listRes.status !== 200) fail(`tools/list: HTTP ${listRes.status}\n${listText.slice(0, 600)}`);
const listMessages = parseSseJsonMessages(listText);
if (verbose) console.log(DIM + JSON.stringify(listMessages, null, 2) + R);
const listMsg = listMessages.find((m) => m.result && Array.isArray(m.result.tools));
if (!listMsg) fail(`tools/list: unexpected SSE\n${listText.slice(0, 800)}`);
const names = listMsg.result.tools.map((t) => t.name);
if (!names.includes(toolName)) fail(`tools/list: missing tool "${toolName}" (have: ${names.slice(0, 8).join(', ')}…)`);
ok(`tools/list (${names.length} tools)`);

step(`MCP tools/call ${toolName}`);
const callBody = {
  jsonrpc: '2.0',
  id: 3,
  method: 'tools/call',
  params: { name: toolName, arguments: toolArgs },
};
const callRes = await fetch(`${mcpBase}/mcp`, {
  method: 'POST',
  headers: {
    ...authHeaders(mcpBearer),
    'Mcp-Session-Id': sessionId,
  },
  body: JSON.stringify(callBody),
});
const callText = await callRes.text();
if (callRes.status !== 200) fail(`tools/call: HTTP ${callRes.status}\n${callText.slice(0, 600)}`);
const callMessages = parseSseJsonMessages(callText);
if (verbose) console.log(DIM + JSON.stringify(callMessages, null, 2) + R);
const callMsg = [...callMessages]
  .reverse()
  .find((m) => m && (m.result !== undefined || m.error !== undefined));
if (!callMsg) fail(`tools/call: no JSON-RPC message in SSE\n${callText.slice(0, 800)}`);
if (callMsg.error) fail(`tools/call RPC error: ${JSON.stringify(callMsg.error)}`);
const r = callMsg.result;
if (!r) fail(`tools/call: empty result`);
if (r.isError === true) {
  const bits = r.content || r.structuredContent || r;
  const errText = JSON.stringify(bits).slice(0, 800);
  const hint =
    /Name or service not known|Errno -2/i.test(errText)
      ? '\n  Hint: the MCP server could not resolve the REST API host. Set EDA_API_BASE in the MCP process (e.g. Docker `.env`) to the same HTTPS base you use for direct API checks (sandbox execute-api, etc.). Default in the app is production api.easydeploy.ai.'
      : '';
  fail(`tools/call tool error: ${errText}${hint}`);
}
ok(`tools/call ${toolName}`);
const preview = JSON.stringify(r).slice(0, 400);
console.log(`  ${DIM}${preview}${preview.length >= 400 ? '…' : ''}${R}`);

console.log(`\n${GREEN}${BOLD}HTTP MCP smoke completed successfully.${R}\n`);
