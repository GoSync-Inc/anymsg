#!/usr/bin/env node
import {readFileSync, openSync, fstatSync, closeSync, constants} from 'node:fs';
import {parseArgs} from 'node:util';
import {DeedKitClient, DeedKitError, toolCatalog, tools} from '../lib/index.js';

const USAGE = 'DeedKit gateway client (an existing DeedKit gateway is required)\n' +
  'Usage: deedkit <tool> --url https://gateway.example --token-file /private/agent.key --json \'{}\'\n' +
  '       deedkit download --url ... --token-file ... --json \'{"attachment_id":"..."}\' --output ./file\n' +
  '       deedkit tools    (catalog with read_only flags; no network)\n' +
  'DEEDKIT_URL and DEEDKIT_TOKEN_FILE may replace --url and --token-file.\n' +
  'Exit codes: 0 success, 1 rejected, 2 usage error, 3 outcome unknown (do not retry).\n' +
  'Tools: ' + tools.join(', ');
const UNKNOWN_NOTICE = 'Outcome unknown: the action may already have happened. Do not retry it or create a ' +
  'replacement request ID; check its status with the original request ID first.';

class Usage extends Error {}

function readToken(file) {
  const fd = openSync(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const stat = fstatSync(fd);
    if (!stat.isFile() || stat.size > 4096 || (process.platform !== 'win32' && (stat.mode & 0o077)))
      throw new Usage('Token must be a regular private file of at most 4096 bytes (chmod 600)');
    return readFileSync(fd, 'utf8').trim();
  } finally { closeSync(fd); }
}

try {
  const {values, positionals} = parseArgs({allowPositionals: true, options: {
    help: {type: 'boolean', short: 'h'}, version: {type: 'boolean', short: 'v'},
    url: {type: 'string'}, 'token-file': {type: 'string'}, json: {type: 'string'}, output: {type: 'string'},
  }});
  const command = positionals[0];
  if (values.version) console.log(JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version);
  else if (values.help || !positionals.length) console.log(USAGE);
  else if (command === 'tools' && positionals.length === 1)
    console.log(JSON.stringify(toolCatalog.map(row => ({name: row.name, read_only: row.readOnly})), null, 2));
  else {
    const url = values.url ?? process.env.DEEDKIT_URL;
    const tokenFile = values['token-file'] ?? process.env.DEEDKIT_TOKEN_FILE;
    if (positionals.length !== 1 || (!tools.includes(command) && command !== 'download') || !url || !tokenFile ||
        (command === 'download') !== Boolean(values.output))
      throw new Usage('Provide one tool (or download with --output), --url and --token-file');
    let args;
    try { args = JSON.parse(values.json ?? '{}'); } catch { throw new Usage('--json must be a JSON object'); }
    let client;
    try { client = new DeedKitClient({baseUrl: url, token: readToken(tokenFile)}); } catch (error) {
      throw error instanceof Usage ? error : new Usage(error instanceof TypeError ? error.message : 'Unreadable token file');
    }
    let result;
    try {
      result = command === 'download'
        ? await client.downloadAttachment(args, values.output) : await client.call(command, args);
    } catch (error) {
      if (error instanceof TypeError) throw new Usage(error.message);
      throw error;
    }
    console.log(JSON.stringify(result, null, 2));
  }
} catch (error) {
  if (error instanceof DeedKitError) {
    const output = error.toJSON();
    if (error.unknownOutcome) output.notice = UNKNOWN_NOTICE;
    console.error(JSON.stringify(output));
    process.exitCode = error.unknownOutcome ? 3 : 1;
  } else {
    // Never echo arguments, paths or token material.
    console.error(JSON.stringify({error: 'usage_error', detail: error instanceof Usage ? error.message : 'invalid_arguments',
      next_action: 'Run deedkit --help; use a private token file and a JSON object.'}));
    process.exitCode = 2;
  }
}
