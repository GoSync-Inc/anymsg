import {test} from 'node:test';
import assert from 'node:assert/strict';
import {inspect} from 'node:util';
import {execFileSync} from 'node:child_process';
import {AnyMsgClient, AnyMsgError} from '../lib/index.js';

const token = 'synthetic-secret-token';
const options = {baseUrl: 'https://archive.example/base', token};
const response = (body, status = 200) => Response.json(body, {status});
test('prefix, auth and JSON contract; token is not enumerable', async () => {
  const client = new AnyMsgClient({...options, fetch: async (url, request) => {
    assert.equal(String(url), 'https://archive.example/base/v1/search');
    assert.equal(request.headers.Authorization, `Bearer ${token}`);
    assert.equal(request.redirect, 'error');
    assert.deepEqual(JSON.parse(request.body), {query: 'contracts'});
    return response({items: [], coverage: {state: 'partial'}});
  }});
  assert.equal((await client.search({query: 'contracts'})).coverage.state, 'partial');
  assert.ok(!inspect(client).includes(token));
  assert.ok(!JSON.stringify(client).includes(token));
});
test('reject unsafe endpoints before sending credentials', () => {
  for (const baseUrl of ['http://remote.example', 'https://user:pass@example.com',
    'https://example.com/?key=abc', 'file:///tmp', 'https://example.com/mcp', 'https://example.com/v1'])
    assert.throws(() => new AnyMsgClient({...options, baseUrl}), TypeError);
  for (const baseUrl of ['http://127.0.0.1:9000', 'http://localhost:9000', 'http://[::1]:9000'])
    assert.doesNotThrow(() => new AnyMsgClient({...options, baseUrl}));
});
test('structured errors retain diagnostic identifiers, never retry writes', async () => {
  let calls = 0;
  const client = new AnyMsgClient({...options, fetch: async () => {
    calls++;
    return response({error: 'version_conflict', request_id: 'a'.repeat(32), retryable: true,
      detail: token}, 409);
  }});
  await assert.rejects(client.ackUpdates({consumer: 'x', receipt: 'fixture', expected_version: 1}),
    error => error instanceof AnyMsgError && error.code === 'version_conflict'
      && error.status === 409 && error.requestId === 'a'.repeat(32) && !String(error).includes(token));
  assert.equal(calls, 1);
});
test('unrecognized remote error strings and request identifiers are redacted', async () => {
  const client = new AnyMsgClient({...options, fetch: async () => response({
    error: 'synthetic_private_caption', request_id: 'synthetic_private_value', detail: token,
  }, 500)});
  await assert.rejects(client.capabilities(), error => error.code === 'http_error'
    && error.requestId === null && !JSON.stringify(error).includes('synthetic_private'));
});
test('refuse redirects and bound streaming responses with no content-length', async () => {
  const redirect = new AnyMsgClient({...options, fetch: async () => new Response('', {status: 302})});
  await assert.rejects(redirect.capabilities(), {code: 'redirect_refused'});
  const large = new AnyMsgClient({...options, maxResponseBytes: 10, fetch: async () => response({body: 'long'})});
  await assert.rejects(large.capabilities(), {code: 'response_too_large'});
});
test('malformed and non-JSON responses have safe errors', async () => {
  for (const value of [new Response('secret'), new Response('{', {headers: {'content-type': 'application/json'}}), response([])]) {
    const client = new AnyMsgClient({...options, fetch: async () => value});
    await assert.rejects(client.capabilities(), {code: 'invalid_response'});
  }
});
test('invalid or oversized requests do not reach fetch', async () => {
  let calls = 0;
  const client = new AnyMsgClient({...options, fetch: async () => {calls++; return response({});}});
  for (const [method, args] of [['send', {}], ['search', []], ['search', {token}],
    ['search', {query: 'a'.repeat(17000)}]]) await assert.rejects(client.call(method, args), TypeError);
  assert.equal(calls, 0);
});
test('timeout covers response consumption and does not leak transport strings', async () => {
  const client = new AnyMsgClient({...options, timeoutMs: 5, fetch: async (_url, {signal}) => {
    return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error(token))));
  }});
  await assert.rejects(client.capabilities(), error => error.code === 'request_aborted' && !String(error).includes(token));
});
test('CLI help and version need neither server nor credentials', () => {
  const path = new URL('../bin/anymsg.js', import.meta.url);
  assert.match(execFileSync(process.execPath, [path.pathname, '--help'], {encoding: 'utf8'}), /existing server/);
  assert.equal(execFileSync(process.execPath, [path.pathname, '--version'], {encoding: 'utf8'}).trim(), '0.9.8');
});
