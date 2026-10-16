import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DeedKitClient, DeedKitError} from '../lib/index.js';

const options = {baseUrl: 'https://gateway.example', token: 'synthetic-gateway-key'};
const replying = (body, status) => new DeedKitClient({...options, fetch: async () => Response.json(body, {status})});

test('gateway error codes reach the caller unchanged with their status', async () => {
  for (const [body, status] of [
    [{error: 'not_found_or_forbidden'}, 403],
    [{error: 'invalid_arguments'}, 422],
    [{error: 'unauthorized'}, 401],
    [{error: 'backend_configuration_requires_restart'}, 503],
    [{error: 'policy_changed'}, 409],
    [{error: 'preview_mismatch', message: 'synthetic private caption'}, 409],
  ]) {
    await assert.rejects(replying(body, status).call('messages_search', {}), error =>
      error instanceof DeedKitError && error.code === body.error && error.status === status &&
      error.outcome === 'rejected' && !JSON.stringify(error).includes('synthetic private'));
  }
});

test('backend_unavailable keeps bounded retry diagnostics and drops free text', async () => {
  const body = {error: 'backend_unavailable', backend_status: 503, request_id: 'b'.repeat(32), phase: 'operation',
    retryable: true, retry_after_seconds: 2, next_action: 'narrow_account_chat_or_date_filters',
    hint: 'Narrow with accounts, then retry once.', reason: 'gateway_timeout', backend_error: 'query_timeout'};
  await assert.rejects(replying(body, 503).call('messages_search', {}), error => {
    assert.deepEqual(error.toJSON(), {error: 'backend_unavailable', status: 503, tool: 'messages_search',
      outcome: 'rejected', retryable: true, automatic_retry: false, request_id: 'b'.repeat(32),
      next_action: 'narrow_account_chat_or_date_filters', retry_after_seconds: 2, phase: 'operation',
      reason: 'gateway_timeout', backend_error: 'query_timeout', backend_status: 503});
    return true;
  });
  const before = {error: 'backend_unavailable', phase: 'capabilities', operation_sent: false,
    gateway_request_id: 'c'.repeat(32), retryable: true, retry_after_seconds: 2, next_action: 'retry_after_delay'};
  await assert.rejects(replying(before, 503).call('messages_send_message', {}), error =>
    error.outcome === 'rejected' && error.operationSent === false && error.retryable &&
    error.gatewayRequestId === 'c'.repeat(32));
});

test('mutation_outcome_unknown is an explicit unknown, never retryable', async () => {
  const body = {schema_version: 1, error: 'mutation_outcome_unknown', outcome: 'unknown', automatic_retry: false,
    backend_status: 502, retryable: true};
  await assert.rejects(replying(body, 503).call('messages_send_message', {}), error => {
    assert.equal(error.code, 'mutation_outcome_unknown');
    assert.equal(error.outcome, 'unknown');
    assert.equal(error.unknownOutcome, true);
    assert.equal(error.retryable, false);
    assert.equal(error.automaticRetry, false);
    assert.equal(error.retryAfterSeconds, null);
    assert.equal(error.nextAction, 'verify_outcome_do_not_retry');
    assert.match(error.message, /do not retry/);
    return true;
  });
});

test('a mutation without a definitive gateway answer is unknown; a read is not', async () => {
  const failures = [
    async () => { throw new Error('synthetic socket reset'); },
    async () => new Response('', {status: 302, headers: {location: 'https://elsewhere.example'}}),
    async () => new Response('<html>bad gateway</html>', {status: 502, headers: {'content-type': 'text/html'}}),
    async () => Response.json({detail: 'proxy failure'}, {status: 504}),
    async () => new Response('{"partial":', {headers: {'content-type': 'application/json'}}),
  ];
  for (const fetch of failures) {
    let calls = 0;
    const counted = async (...args) => { calls++; return fetch(...args); };
    const client = new DeedKitClient({...options, fetch: counted});
    await assert.rejects(client.call('messages_send_message', {request_id: 'synthetic-0001'}), error =>
      error instanceof DeedKitError && error.outcome === 'unknown' && error.retryable === false &&
      !error.message.includes('synthetic socket'));
    await assert.rejects(client.call('calls_start_call', {}), {outcome: 'unknown'});
    await assert.rejects(client.call('messages_search', {}), {outcome: 'rejected'});
    assert.equal(calls, 3, 'the client never retries');
  }
});

test('a timed-out mutation is unknown', async () => {
  const client = new DeedKitClient({...options, timeoutMs: 20, fetch: async (url, request) =>
    new Promise((resolve, reject) => request.signal.addEventListener('abort', () => reject(new Error('abort'))))});
  await assert.rejects(client.call('calendar_apply', {}), {code: 'request_aborted', outcome: 'unknown'});
});

test('non-code error strings and identifiers are redacted', async () => {
  const body = {error: 'Synthetic private caption!', request_id: 'synthetic_private_value', next_action: 'Do X now',
    phase: 'contains spaces', backend_status: 'x'};
  await assert.rejects(replying(body, 400).call('messages_search', {}), error => {
    assert.equal(error.code, 'http_error');
    assert.equal(error.requestId, null);
    assert.equal(error.nextAction, null);
    assert.equal(error.phase, null);
    assert.equal(error.backendStatus, null);
    assert.ok(!JSON.stringify(error).includes('ynthetic'));
    return true;
  });
});

test('a coded 5xx is unknown for a mutation unless the gateway answered before dispatch', async () => {
  await assert.rejects(replying({error: 'gateway_timeout', retryable: true}, 504).call('messages_send_message', {}),
    error => error.code === 'gateway_timeout' && error.outcome === 'unknown' && error.retryable === false);
  await assert.rejects(replying({error: 'gateway_timeout', retryable: true}, 504).call('messages_search', {}),
    {code: 'gateway_timeout', outcome: 'rejected', retryable: true});
  for (const code of ['backend_busy', 'backend_configuration_requires_restart', 'backend_capability_contract_unavailable',
    'policy_unavailable'])
    await assert.rejects(replying({error: code}, 503).call('calendar_apply', {}), {code, outcome: 'rejected'});
  await assert.rejects(replying({error: 'backend_unavailable', phase: 'operation'}, 503).call('calendar_apply', {}),
    {code: 'backend_unavailable', outcome: 'unknown'});
});

test('a release switch answer is retryable after its Retry-After', async () => {
  const client = new DeedKitClient({...options, fetch: async () => new Response('{"error":"release_unavailable"}',
    {status: 503, headers: {'content-type': 'application/json', 'retry-after': '1'}})});
  await assert.rejects(client.call('messages_send_message', {}), error => error.code === 'release_unavailable' &&
    error.outcome === 'rejected' && error.retryable === true && error.retryAfterSeconds === 1);
});

test('a failing body cancel never replaces the classified error', async () => {
  for (const failing of [true, false]) {
    for (const [status, headers] of [[302, {location: 'https://elsewhere.example'}],
      [200, {'content-type': 'text/html'}], [200, {'content-type': 'application/json', 'content-length': '999999999'}]]) {
      let cancelled = false;
      const body = new ReadableStream({pull() {}, cancel() {
        cancelled = true;
        if (failing) throw new Error('synthetic cancel failure');
      }});
      const client = new DeedKitClient({...options, fetch: async () => new Response(body, {status, headers})});
      await assert.rejects(client.call('messages_send_message', {}), error => error instanceof DeedKitError &&
        error.outcome === 'unknown' && !error.message.includes('synthetic cancel'));
      assert.ok(cancelled, `body cancelled for ${status}`);
    }
  }
});
