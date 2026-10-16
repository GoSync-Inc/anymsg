import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {existsSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DeedKitClient} from '../lib/index.js';

const token = 'synthetic-gateway-key';
const ticket = 'T'.repeat(43);
const bytes = Buffer.from('synthetic original file bytes');
const sha256 = createHash('sha256').update(bytes).digest('hex');
const grant = (url = `https://gateway.example/deedkit/v1/files/${ticket}`, extra = {}) =>
  ({download_url: url, download_expires_in: 60, bytes: bytes.length, sha256, filename: 'fixture.txt', ...extra});
const fileResponse = (body = bytes, headers = {'content-length': String(body.length)}) =>
  new Response(body, {status: 200, headers: {'content-type': 'application/octet-stream', ...headers}});
const directory = () => mkdtempSync(join(tmpdir(), 'deedkit-download-'));

test('downloads a ticket into a new private file without sending the gateway key', async () => {
  const dir = directory();
  try {
    const requests = [];
    const client = new DeedKitClient({baseUrl: 'https://gateway.example/deedkit', token, fetch: async (url, request) => {
      requests.push([String(url), request]);
      if (String(url).endsWith('/v1/messages_get_attachment')) return Response.json(grant());
      return fileResponse();
    }});
    const output = join(dir, 'file.bin');
    const result = await client.downloadAttachment({attachment_id: 'fixture-attachment'}, output);
    assert.deepEqual(result, {path: output, bytes: bytes.length, sha256, filename: 'fixture.txt'});
    assert.deepEqual(readFileSync(output), bytes);
    assert.equal(statSync(output).mode & 0o777, 0o600);
    const [url, request] = requests[1];
    assert.equal(url, `https://gateway.example/deedkit/v1/files/${ticket}`);
    assert.equal(request.method, 'GET');
    assert.equal(request.redirect, 'error');
    assert.ok(!JSON.stringify(request.headers).includes(token));
  } finally { rmSync(dir, {recursive: true, force: true}); }
});

test('accepts the release-scoped ticket path of the same gateway', async () => {
  const dir = directory();
  try {
    const client = new DeedKitClient({baseUrl: 'https://gateway.example/', token, fetch: async () => fileResponse()});
    const output = join(dir, 'file.bin');
    await client.downloadFile(grant(`https://gateway.example/_releases/0123456789ab/v1/files/${ticket}`), output);
    assert.deepEqual(readFileSync(output), bytes);
  } finally { rmSync(dir, {recursive: true, force: true}); }
});

test('refuses tickets outside the configured gateway before any request', async () => {
  const dir = directory();
  try {
    let calls = 0;
    const client = new DeedKitClient({baseUrl: 'https://gateway.example/deedkit', token,
      fetch: async () => { calls++; return fileResponse(); }});
    for (const url of [`https://evil.example/deedkit/v1/files/${ticket}`, `http://gateway.example/deedkit/v1/files/${ticket}`,
      `https://gateway.example/other/v1/files/${ticket}`, `https://gateway.example/deedkit/v1/messages_search`,
      `https://gateway.example/deedkit/v1/files/${ticket}?x=1`, `https://u:p@gateway.example/deedkit/v1/files/${ticket}`,
      `https://gateway.example/deedkit/v1/files/short`, 'not a url'])
      await assert.rejects(client.downloadFile(grant(url), join(dir, 'x')), {code: 'invalid_download_url'});
    for (const bad of [null, {download_url: 'x'}, grant(undefined, {sha256: 'ABC'}), grant(undefined, {bytes: -1})])
      await assert.rejects(client.downloadFile(bad, join(dir, 'x')), TypeError);
    assert.equal(calls, 0);
    assert.ok(!existsSync(join(dir, 'x')));
  } finally { rmSync(dir, {recursive: true, force: true}); }
});

test('never overwrites an existing file', async () => {
  const dir = directory();
  try {
    const output = join(dir, 'existing');
    writeFileSync(output, 'keep');
    const client = new DeedKitClient({baseUrl: 'https://gateway.example/deedkit', token, fetch: async () => fileResponse()});
    await assert.rejects(client.downloadFile(grant(), output), {code: 'output_unavailable'});
    assert.equal(readFileSync(output, 'utf8'), 'keep');
  } finally { rmSync(dir, {recursive: true, force: true}); }
});

test('integrity failures and gateway errors leave no partial file', async () => {
  const dir = directory();
  try {
    const altered = Buffer.from(bytes);
    altered[0] ^= 1;
    for (const [fetch, code] of [
      [async () => fileResponse(altered), 'download_integrity_error'],
      [async () => fileResponse(Buffer.concat([bytes, bytes]), {}), 'download_integrity_error'],
      [async () => fileResponse(bytes.subarray(1), {}), 'download_integrity_error'],
      [async () => fileResponse(bytes, {'content-length': '1'}), 'download_integrity_error'],
      [async () => Response.json({error: 'not_found'}, {status: 404}), 'not_found'],
      [async () => Response.json({error: 'ranges_not_supported'}, {status: 416}), 'ranges_not_supported'],
      [async () => new Response('', {status: 302}), 'redirect_refused'],
      [async () => { throw new Error('synthetic reset'); }, 'transport_error'],
      [async () => new Response(new ReadableStream({pull(controller) { controller.error(new Error('cut')); }}),
        {status: 200}), 'transport_error'],
    ]) {
      const output = join(dir, 'partial');
      const client = new DeedKitClient({baseUrl: 'https://gateway.example/deedkit', token, fetch});
      await assert.rejects(client.downloadFile(grant(), output), {code});
      assert.ok(!existsSync(output), code);
    }
  } finally { rmSync(dir, {recursive: true, force: true}); }
});
