#!/usr/bin/env node
/* eslint-env node */
/* global process */
/**
 * EasyDeploy AI — HTTP MCP full smoke: train a model + ad-hoc prediction
 *
 * Drives the same path as accessible-ai/scripts/smoke-public-api.mjs, but every
 * step is tools/call on Streamable HTTP /mcp (plus one gateway PUT for CSV upload).
 *
 * Usage:
 *   node scripts/smoke-mcp-train-predict.mjs --file /path/to/Breast_Cancer_Wisconcin_ds.csv
 *   node scripts/smoke-mcp-train-predict.mjs --file ./data.csv --target diagnosis --project-name "My QA"
 *
 * Env (bearer for /mcp — same as smoke-mcp-http.mjs):
 *   MCP_SMOKE_BASE_URL, EDA_SMOKE_ACCESS_TOKEN, EDA_INTEGRATION_COGNITO_ACCESS_TOKEN,
 *   EDA_API_KEY, EDA_SMOKE_API_KEY, MCP_SERVICE_TOKEN
 *
 * Optional env:
 *   EDA_SMOKE_CSV              Default CSV path when --file omitted
 *   EDA_PROJECT_ID             Use existing project (same as --project-id)
 *   EDA_MCP_TRAIN_PROJECT_NAME Default project name if no --project-name
 *
 * Requires Node 18+.
 */

import { existsSync, readFileSync, statSync } from 'fs';
import { basename, dirname, extname, resolve } from 'path';
import { fileURLToPath } from 'url';

import {
  mcpCallTool,
  mcpOpenSession,
  mcpToolNames,
  resolveMcpBearer,
} from './mcp-streamable-client.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));

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

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

function humanize(raw) {
  return raw
    .replace(/[_-]+/g, ' ')
    .replace(/\s+_?ds\s*$/i, '')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

const SAMPLE_CSV = 'Breast_Cancer_Wisconcin_ds.csv';

function resolveDefaultCsv() {
  const candidates = [
    process.env.EDA_SMOKE_CSV,
    resolve(__dirname, `../test/data/${SAMPLE_CSV}`),
    resolve(__dirname, `../../../Amplify/accessible-ai/test/data/${SAMPLE_CSV}`),
    resolve(__dirname, `../../../AAI/accessible-ai-cdk/test/data/${SAMPLE_CSV}`),
    resolve(process.cwd(), `test/data/${SAMPLE_CSV}`),
  ].filter(Boolean);
  for (const p of candidates) {
    const abs = resolve(p);
    if (existsSync(abs)) return abs;
  }
  return null;
}

/**
 * Same ad-hoc row as accessible-ai/scripts/smoke-public-api.mjs.
 * Wisconsin CSV uses spaces in three column names; the API matches training feature names exactly.
 */
const ADHOC_INPUT = {
  id: 842302,
  radius_mean: 17.99,
  texture_mean: 10.38,
  perimeter_mean: 122.8,
  area_mean: 1001,
  smoothness_mean: 0.1184,
  compactness_mean: 0.2776,
  concavity_mean: 0.3001,
  concave_points_mean: 0.1471,
  'concave points_mean': 0.1471,
  symmetry_mean: 0.2419,
  fractal_dimension_mean: 0.07871,
  radius_se: 1.095,
  texture_se: 0.9053,
  perimeter_se: 8.589,
  area_se: 153.4,
  smoothness_se: 0.006399,
  compactness_se: 0.04904,
  concavity_se: 0.05373,
  concave_points_se: 0.01587,
  'concave points_se': 0.01587,
  symmetry_se: 0.03003,
  fractal_dimension_se: 0.006193,
  radius_worst: 25.38,
  texture_worst: 17.33,
  perimeter_worst: 184.6,
  area_worst: 2019,
  smoothness_worst: 0.1622,
  compactness_worst: 0.6656,
  concavity_worst: 0.7119,
  concave_points_worst: 0.2654,
  'concave points_worst': 0.2654,
  symmetry_worst: 0.4601,
  fractal_dimension_worst: 0.1189,
};

function parseGatewayUploadCurl(curlCommand) {
  const tokenM = curlCommand.match(/X-Upload-Token:\s*([^"]+)/);
  const urlM = curlCommand.match(/-T\s+"FILE_PATH"\s+"([^"]+)"/);
  if (!tokenM || !urlM) {
    fail(
      `Could not parse curl_command for gateway upload.\n${DIM}${curlCommand.slice(0, 200)}${R}`,
    );
  }
  return { uploadToken: tokenM[1].trim(), url: urlM[1] };
}

async function main() {
  const { flags } = parseArgs(process.argv.slice(2));
  const mcpBase = (
    flags['mcp-base'] ||
    process.env.MCP_SMOKE_BASE_URL ||
    'http://127.0.0.1:8080'
  ).replace(/\/$/, '');

  const noPoll = flags['no-poll'] === true;
  const verbose = flags.verbose === true;
  const trainingTimeoutMin = Math.max(
    1,
    parseInt(String(flags['training-timeout-min'] || '30'), 10) || 30,
  );
  const pollIntervalSec = Math.max(
    5,
    parseInt(String(flags['training-poll-seconds'] || '15'), 10) || 15,
  );

  const targetCol =
    typeof flags.target === 'string' ? flags.target : 'diagnosis';

  const filePathFlag = flags.file;
  let resolvedCsv;
  if (filePathFlag) {
    resolvedCsv = resolve(String(filePathFlag));
    if (!existsSync(resolvedCsv)) fail(`File not found: ${resolvedCsv}`);
  } else {
    resolvedCsv = resolveDefaultCsv();
    if (!resolvedCsv) {
      fail(
        `No CSV found. Pass --file <path> or set EDA_SMOKE_CSV, or add ${SAMPLE_CSV} under test/data (see accessible-ai / accessible-ai-cdk).`,
      );
    }
  }

  const mcpBearer = resolveMcpBearer(flags);

  console.log(`\n${BOLD}${CYAN}EasyDeploy AI — HTTP MCP train + predict smoke${R}`);
  info(`MCP base: ${DIM}${mcpBase}${R}`);
  info(`CSV: ${DIM}${resolvedCsv}${R}`);
  info(`Target column: ${DIM}${targetCol}${R}`);

  step('GET /healthz');
  {
    const res = await fetch(`${mcpBase}/healthz`);
    const body = await res.json().catch(() => ({}));
    if (res.status !== 200 || body.status !== 'ok') {
      fail(`healthz: ${res.status} ${JSON.stringify(body)}`);
    }
    ok('healthz');
  }

  step('GET /.well-known/oauth-protected-resource');
  let oauthOn = false;
  {
    const res = await fetch(`${mcpBase}/.well-known/oauth-protected-resource`);
    if (res.status === 404) {
      info('404 — OAuth disabled (legacy mode)');
    } else if (res.status === 200) {
      const body = await res.json().catch(() => ({}));
      if (body.authorization_servers) {
        oauthOn = true;
        ok(`OAuth on (issuer: ${body.authorization_servers[0]})`);
      }
    }
  }
  if (oauthOn && !mcpBearer) {
    fail(
      'OAuth enabled but no bearer. Set EDA_SMOKE_ACCESS_TOKEN, EDA_API_KEY, etc., or --bearer <token>.',
    );
  }

  step('MCP session');
  const sessionId = await mcpOpenSession(mcpBase, mcpBearer);
  ok(`session ${sessionId.slice(0, 8)}…`);

  const names = await mcpToolNames(mcpBase, mcpBearer, sessionId);
  const need = [
    'create_project',
    'list_projects',
    'start_upload',
    'complete_upload',
    'create_model',
    'list_models',
    'create_model_version',
    'submit_training_job',
    'get_model_version',
    'run_prediction',
    'list_predictions',
  ];
  const missing = need.filter((n) => !names.includes(n));
  if (missing.length) {
    fail(`MCP server is missing tools: ${missing.join(', ')}`);
  }
  ok(`tool catalog OK (${names.length} tools)`);

  let rpcId = 3;
  const tool = async (name, args) => {
    const id = rpcId++;
    if (verbose) console.log(DIM + `tools/call ${name} ${JSON.stringify(args)}` + R);
    return mcpCallTool(mcpBase, mcpBearer, sessionId, id, name, args);
  };

  const targetTitle = humanize(targetCol);
  const modelName = `${targetTitle} Predictor`;
  const filename = basename(resolvedCsv);
  const fileSize = statSync(resolvedCsv).size;
  const dsTitle = humanize(basename(resolvedCsv, extname(resolvedCsv)));

  // --- Project
  step('Project');
  let projectId =
    (flags['project-id'] && String(flags['project-id'])) ||
    (process.env.EDA_PROJECT_ID && String(process.env.EDA_PROJECT_ID).trim()) ||
    '';
  const projectName =
    (flags['project-name'] && String(flags['project-name']).trim()) ||
    (process.env.EDA_MCP_TRAIN_PROJECT_NAME &&
      String(process.env.EDA_MCP_TRAIN_PROJECT_NAME).trim()) ||
    '';

  if (projectId) {
    await tool('get_project', { project_id: projectId });
    ok(`using project ${DIM}${projectId}${R}`);
  } else {
    const wanted = (projectName || `MCP smoke train ${Date.now()}`).trim();
    const projects = await tool('list_projects', {});
    const list = Array.isArray(projects) ? projects : [];
    const found = list.find(
      (p) => String(p.name || '').trim().toLowerCase() === wanted.toLowerCase(),
    );
    if (found && found.id) {
      projectId = String(found.id);
      ok(`reuse project ${BOLD}${found.name}${R} ${DIM}${projectId}${R}`);
    } else {
      const desc = `${targetTitle} prediction project (MCP smoke)`;
      const created = await tool('create_project', { name: wanted, description: desc });
      projectId = String(created.id || '').trim();
      if (!projectId) fail('create_project: no id in response');
      ok(`created project ${BOLD}${created.name || wanted}${R} ${DIM}${projectId}${R}`);
    }
  }

  // --- Upload
  step('Upload (gateway PUT + complete_upload)');
  const up = await tool('start_upload', { filename, project_id: projectId });
  const curlCommand = up.curl_command || up.curlCommand;
  if (!curlCommand) fail('start_upload: missing curl_command');
  const { uploadToken, url: gatewayUrl } = parseGatewayUploadCurl(curlCommand);
  const uploadRequestId = up.upload_request_id || up.uploadRequestId;
  const datasetIdHint = String(up.datasetId || up.dataset_id || '').trim();

  const buf = readFileSync(resolvedCsv);
  const putRes = await fetch(gatewayUrl, {
    method: 'PUT',
    headers: {
      'Content-Type': 'text/csv',
      'X-Upload-Token': uploadToken,
    },
    body: buf,
  });
  if (!putRes.ok) {
    const t = await putRes.text();
    fail(`gateway PUT failed: HTTP ${putRes.status} ${t.slice(0, 300)}`);
  }
  ok(`uploaded ${filename} (${(fileSize / 1024).toFixed(1)} KB)`);

  const completeArgs = {
    project_id: projectId,
    name: dsTitle,
    upload_request_id: uploadRequestId,
    description: `Training dataset for ${targetTitle.toLowerCase()} (MCP smoke)`,
    dataset_type: 'train',
  };
  if (datasetIdHint) completeArgs.dataset_id = datasetIdHint;

  const completed = await tool('complete_upload', completeArgs);
  const dv =
    completed.datasetVersion ||
    completed.dataset_version ||
    (completed.data &&
      (completed.data.datasetVersion || completed.data.dataset_version));
  const datasetVersionId = dv && String(dv.id || '').trim();
  if (!datasetVersionId) {
    fail(`complete_upload: missing dataset version id\n${DIM}${JSON.stringify(completed).slice(0, 400)}${R}`);
  }
  ok(`dataset version ${DIM}${datasetVersionId}${R}`);

  // --- Model
  step('Model');
  const models = await tool('list_models', { project_id: projectId });
  const mlist = Array.isArray(models) ? models : [];
  let existing = mlist.find(
    (m) => String(m.name || '').trim().toLowerCase() === modelName.toLowerCase(),
  );
  let modelId;
  if (existing && existing.id) {
    modelId = String(existing.id);
    info(`reuse model ${BOLD}${existing.name}${R} ${DIM}${modelId}${R}`);
  } else {
    const m = await tool('create_model', {
      project_id: projectId,
      name: modelName,
      description: `Predicts ${targetTitle.toLowerCase()} (MCP smoke)`,
    });
    modelId = String(m.id || '').trim();
    if (!modelId) fail('create_model: no id');
    ok(`model ${BOLD}${m.name || modelName}${R} ${DIM}${modelId}${R}`);
  }

  // --- Version + train
  step('Model version + training job');
  const mv = await tool('create_model_version', {
    project_id: projectId,
    model_id: modelId,
    dataset_version_id: datasetVersionId,
    target_feature: targetCol,
  });
  const modelVersionId = String(mv.id || '').trim();
  if (!modelVersionId) fail('create_model_version: no id');
  ok(`model version v${mv.version ?? '?'} ${DIM}${modelVersionId}${R}`);

  const job = await tool('submit_training_job', { model_version_id: modelVersionId });
  const jobId = job.jobId || job.job_id;
  if (jobId) info(`training job ${DIM}${jobId}${R}`);

  let finalMv = mv;
  if (!noPoll) {
    step(`Wait for TRAINING_COMPLETED (≤ ${trainingTimeoutMin} min, poll ${pollIntervalSec}s)`);
    const deadline = Date.now() + trainingTimeoutMin * 60_000;
    process.stdout.write(`  ${DIM}`);
    while (Date.now() < deadline) {
      finalMv = await tool('get_model_version', {
        project_id: projectId,
        model_id: modelId,
        version_id: modelVersionId,
      });
      const st = String(finalMv.status || '').toUpperCase();
      if (st === 'TRAINING_COMPLETED') {
        process.stdout.write(`${R}\n`);
        ok('training completed');
        break;
      }
      if (st === 'TRAINING_FAILED') {
        process.stdout.write(`${R}\n`);
        fail(`training failed: ${finalMv.status}`);
      }
      process.stdout.write('.');
      await sleep(pollIntervalSec * 1000);
    }
    if (String(finalMv.status || '').toUpperCase() !== 'TRAINING_COMPLETED') {
      process.stdout.write(`${R}\n`);
      fail(`timeout after ${trainingTimeoutMin}m (last status: ${finalMv.status})`);
    }
  } else {
    info('Skipping training poll (--no-poll). Prediction may fail if model is not ready.');
  }

  // --- Prediction
  step('Ad-hoc prediction (run_prediction)');
  const pred = await tool('run_prediction', {
    model_version_id: modelVersionId,
    input_data: ADHOC_INPUT,
    project_id: projectId,
    wait_for_result: !noPoll,
    max_wait_seconds: noPoll ? 60 : 300,
    poll_interval_seconds: 2,
  });
  if (noPoll) {
    ok(`prediction started ${DIM}${pred.prediction_id || pred.id || ''}${R}`);
  } else {
    ok('prediction finished');
    const out =
      pred.output ||
      pred.adhoc_output ||
      pred.adhocOutput ||
      pred.label ||
      pred;
    if (out && typeof out === 'object') {
      console.log(`  ${DIM}${JSON.stringify(out).slice(0, 200)}${R}`);
    }
  }

  step('list_predictions');
  const preds = await tool('list_predictions', { project_id: projectId });
  const n = Array.isArray(preds) ? preds.length : 0;
  ok(`list_predictions (${n} items)`);

  console.log(`\n${GREEN}${BOLD}MCP train + predict smoke completed successfully.${R}\n`);
}

main().catch((e) => {
  console.error(`${RED}Error:${R}`, e.message || e);
  if (/Name or service not known|Errno -2/i.test(String(e.message || e))) {
    console.error(
      `${DIM}Hint: set EDA_API_BASE in the MCP server env (Docker .env) to your sandbox execute-api URL.${R}`,
    );
  }
  process.exit(1);
});
