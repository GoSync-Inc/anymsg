#!/usr/bin/env node
import {readFileSync, openSync, fstatSync, closeSync, constants} from 'node:fs';
import {parseArgs} from 'node:util';
import {AnyMsgClient, AnyMsgError, tools} from '../lib/index.js';

try {
  const {values, positionals} = parseArgs({allowPositionals: true, options: {
    help: {type: 'boolean', short: 'h'}, version: {type: 'boolean', short: 'v'},
    url: {type: 'string'}, 'token-file': {type: 'string'}, json: {type: 'string'},
  }});
  if (values.version) console.log(JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version);
  else if (values.help || !positionals.length) console.log(
    'AnyMsg HTTP client (an existing server is required)\n' +
    'Usage: anymsg <operation> --url https://host/base --token-file /private/key --json \'{}\'\n' +
    'Operations: ' + tools.join(', '));
  else {
    if (positionals.length !== 1 || !tools.includes(positionals[0]) || !values.url || !values['token-file'])
      throw new TypeError('Provide one operation, --url and --token-file');
    const file = values['token-file'];
    const fd = openSync(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    let token;
    try {
      const stat = fstatSync(fd);
      if (!stat.isFile() || stat.size > 4096 || (process.platform !== 'win32' && (stat.mode & 0o077)))
        throw new TypeError('Token must be a regular private file of at most 4096 bytes (chmod 600)');
      token = readFileSync(fd, 'utf8').trim();
    } finally { closeSync(fd); }
    const client = new AnyMsgClient({baseUrl: values.url, token});
    const result = await client.call(positionals[0], JSON.parse(values.json ?? '{}'));
    console.log(JSON.stringify(result, null, 2));
  }
} catch (error) {
  const output = error instanceof AnyMsgError
    ? {error: error.code, status: error.status, request_id: error.requestId, retryable: error.retryable}
    : {error: 'invalid_arguments', next_action: 'Run anymsg --help; use a private token file and valid JSON.'};
  console.error(JSON.stringify(output));
  process.exitCode = 1;
}
