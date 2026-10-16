import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync} from 'node:fs';
import {open} from 'node:fs/promises';
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
      assert.deepEqual(readdirSync(dir), [], code);
    }
  } finally { rmSync(dir, {recursive: true, force: true}); }
});

test('short writes are completed and a stalled write leaves no file', async () => {
  const dir = directory();
  const probe = await open(join(dir, 'probe'), 'w');
  const prototype = Object.getPrototypeOf(probe);
  await probe.close();
  const original = prototype.write;
  try {
    // At most 5 bytes per call, as a nearly full disk may do.
    prototype.write = function(buffer, offset, length) { return original.call(this, buffer, offset, Math.min(length, 5)); };
    const client = new DeedKitClient({baseUrl: 'https://gateway.example/deedkit', token, fetch: async () => fileResponse()});
    const output = join(dir, 'short.bin');
    await client.downloadFile(grant(), output);
    assert.deepEqual(readFileSync(output), bytes);
    prototype.write = async function() { return {bytesWritten: 0}; };
    const stalled = join(dir, 'stalled.bin');
    await assert.rejects(client.downloadFile(grant(), stalled), {code: 'output_unavailable'});
    assert.ok(!existsSync(stalled));
  } finally { prototype.write = original; rmSync(dir, {recursive: true, force: true}); }
});

test('cleanup never removes a file that replaced the output path', async () => {
  const dir = directory();
  try {
    // The output path is never used before verification: a file placed there during a
    // failed or a successful transfer survives, and no staging file is left behind.
    const output = join(dir, 'file.bin');
    const failing = async () => new Response(new ReadableStream({pull(controller) {
      writeFileSync(output, 'someone else');
      controller.error(new Error('cut'));
    }}), {status: 200});
    const client = new DeedKitClient({baseUrl: 'https://gateway.example/deedkit', token, fetch: failing});
    await assert.rejects(client.downloadFile(grant(), output), {code: 'transport_error'});
    assert.equal(readFileSync(output, 'utf8'), 'someone else');
    assert.deepEqual(readdirSync(dir), ['file.bin']);
    const late = join(dir, 'late.bin');
    const racing = async () => new Response(new ReadableStream({pull(controller) {
      writeFileSync(late, 'arrived first');
      controller.enqueue(bytes);
      controller.close();
    }}), {status: 200});
    const raced = new DeedKitClient({baseUrl: 'https://gateway.example/deedkit', token, fetch: racing});
    await assert.rejects(raced.downloadFile(grant(), late), {code: 'output_unavailable'});
    assert.equal(readFileSync(late, 'utf8'), 'arrived first');
    assert.deepEqual(readdirSync(dir).sort(), ['file.bin', 'late.bin']);
  } finally { rmSync(dir, {recursive: true, force: true}); }
});

test('an attachment without an available original is not a usage error', async () => {
  const dir = directory();
  try {
    let calls = 0;
    const client = new DeedKitClient({baseUrl: 'https://gateway.example/deedkit', token, fetch: async () => {
      calls++;
      return Response.json({attachment_id: 'fixture', status: 'pending'});
    }});
    const output = join(dir, 'x');
    await assert.rejects(client.downloadAttachment({attachment_id: 'fixture'}, output),
      {code: 'attachment_not_downloadable', reason: 'pending', outcome: 'rejected'});
    assert.equal(calls, 1);
    assert.ok(!existsSync(output));
  } finally { rmSync(dir, {recursive: true, force: true}); }
});
