import {createHash} from 'node:crypto';
import {constants, readFileSync} from 'node:fs';
import {open, unlink} from 'node:fs/promises';

// The reviewed gateway catalog (services/gateway/src/agent_gateway/catalog.json)
// plus platform_capabilities. A drift test keeps this list equal to the catalog.
const CATALOG = Object.freeze(JSON.parse(readFileSync(new URL('./tools.json', import.meta.url), 'utf8'))
  .map(row => Object.freeze({name: row.name, readOnly: row.read_only === true})));
const READ_ONLY = new Map(CATALOG.map(row => [row.name, row.readOnly]));
export const tools = Object.freeze(CATALOG.map(row => row.name));
export const toolCatalog = CATALOG;

const REQUEST_BYTES = 65536;
const UPLOAD_CHUNK_TOOL = 'messages_upload_file_chunk';
const UPLOAD_REQUEST_BYTES = 360448;
const CODE = /^[a-z][a-z0-9_]{0,63}$/;
const REQUEST_ID = /^(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$/i;
export const UNKNOWN_OUTCOME = 'mutation_outcome_unknown';
const DO_NOT_RETRY = 'verify_outcome_do_not_retry';

export class DeedKitError extends Error {
  constructor(code, {status = 0, tool = null, outcome = 'rejected', retryable = false, requestId = null,
    gatewayRequestId = null, nextAction = null, retryAfterSeconds = null, operationSent = null,
    phase = null, reason = null, backendError = null, backendStatus = null} = {}) {
    super(outcome === 'unknown'
      ? `DeedKit ${tool ?? 'request'} outcome is unknown (${code}); do not retry`
      : `DeedKit request failed: ${code}`);
    this.name = 'DeedKitError';
    this.code = code;
    this.status = status;
    this.tool = tool;
    this.outcome = outcome;
    // An unknown mutation outcome is never retryable (SENDING.md): the action may
    // already have happened. Check its status with the original request ID.
    this.retryable = outcome === 'unknown' ? false : retryable;
    this.automaticRetry = false;
    this.requestId = requestId;
    this.gatewayRequestId = gatewayRequestId;
    this.nextAction = outcome === 'unknown' ? nextAction ?? DO_NOT_RETRY : nextAction;
    this.retryAfterSeconds = this.retryable ? retryAfterSeconds : null;
    this.operationSent = operationSent;
    this.phase = phase;
    this.reason = reason;
    this.backendError = backendError;
    this.backendStatus = backendStatus;
  }
  get unknownOutcome() { return this.outcome === 'unknown'; }
  toJSON() {
    const result = {error: this.code, status: this.status, tool: this.tool, outcome: this.outcome,
      retryable: this.retryable, automatic_retry: false};
    for (const [key, value] of [['request_id', this.requestId], ['gateway_request_id', this.gatewayRequestId],
      ['next_action', this.nextAction], ['retry_after_seconds', this.retryAfterSeconds],
      ['operation_sent', this.operationSent], ['phase', this.phase], ['reason', this.reason],
      ['backend_error', this.backendError], ['backend_status', this.backendStatus]])
      if (value !== null) result[key] = value;
    return result;
  }
}

function base(value) {
  let url;
  try { url = new URL(value); } catch { throw new TypeError('Invalid DeedKit gateway URL'); }
  if (url.username || url.password || url.search || url.hash)
    throw new TypeError('Gateway URL must not contain credentials, query or fragment');
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && local))
    throw new TypeError('Use HTTPS, or HTTP on loopback for local development');
  url.pathname = url.pathname.replace(/\/+$/, '') + '/';
  if (/\/(mcp|v1)\/$/.test(url.pathname))
    throw new TypeError('Gateway URL is the public root, without /mcp or /v1');
  return url;
}

function plainObject(value) {
  return value !== null && typeof value === 'object' && [Object.prototype, null].includes(Object.getPrototypeOf(value));
}

function code(value) { return typeof value === 'string' && CODE.test(value) ? value : null; }

// Keep only bounded machine fields from a gateway error body. Free text (hints,
// backend bodies, message content) never reaches the error object or its log line.
function gatewayError(tool, status, body) {
  const known = code(body.error);
  const unknown = known === UNKNOWN_OUTCOME || body.outcome === 'unknown' || body.unknown_outcome === true ||
    (READ_ONLY.get(tool) === false && status >= 500 && !known);
  const integer = (value, maximum) => Number.isSafeInteger(value) && value >= 0 && value <= maximum ? value : null;
  const requestId = typeof body.request_id === 'string' && REQUEST_ID.test(body.request_id) ? body.request_id : null;
  const gatewayRequestId = typeof body.gateway_request_id === 'string' && /^[0-9a-f]{32}$/.test(body.gateway_request_id)
    ? body.gateway_request_id : null;
  return new DeedKitError(known ?? 'http_error', {status, tool, outcome: unknown ? 'unknown' : 'rejected',
    retryable: body.retryable === true, requestId, gatewayRequestId, nextAction: code(body.next_action),
    retryAfterSeconds: typeof body.retry_after_seconds === 'number' && Number.isFinite(body.retry_after_seconds) &&
      body.retry_after_seconds >= 0 && body.retry_after_seconds <= 3600 ? body.retry_after_seconds : null,
    operationSent: typeof body.operation_sent === 'boolean' ? body.operation_sent : null,
    phase: code(body.phase), reason: code(body.reason), backendError: code(body.backend_error),
    backendStatus: integer(body.backend_status, 599)});
}

async function boundedJson(response, maximum) {
  const length = response.headers.get('content-length');
  if (length && /^\d+$/.test(length) && Number(length) > maximum) {
    await response.body?.cancel();
    throw new DeedKitError('response_too_large', {status: response.status});
  }
  if (!response.headers.get('content-type')?.toLowerCase().includes('application/json')) {
    await response.body?.cancel();
    throw new DeedKitError('invalid_response', {status: response.status});
  }
  const reader = response.body?.getReader();
  if (!reader) throw new DeedKitError('invalid_response', {status: response.status});
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maximum) {
        await reader.cancel();
        throw new DeedKitError('response_too_large', {status: response.status});
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  try {
    const value = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(Buffer.concat(chunks, total)));
    if (!plainObject(value)) throw new Error();
    return value;
  } catch { throw new DeedKitError('invalid_response', {status: response.status}); }
}

function finiteJson(value, depth = 0) {
  if (depth > 64) return false;
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true;
  if (typeof value === 'number') return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(item => finiteJson(item, depth + 1));
  return plainObject(value) && Object.values(value).every(item => finiteJson(item, depth + 1));
}

export class DeedKitClient {
  #base;
  #token;
  #fetch;
  #timeout;
  #maximum;
  constructor({baseUrl, token, timeoutMs = 45000, maxResponseBytes = 16 * 1024 * 1024,
    fetch: fetchImpl = globalThis.fetch} = {}) {
    this.#base = base(baseUrl);
    // The gateway accepts at most 512 visible characters without whitespace.
    if (typeof token !== 'string' || !/^[!-~]{1,512}$/.test(token))
      throw new TypeError('A gateway key of 1 to 512 visible characters is required');
    if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 120000)
      throw new TypeError('timeoutMs must be an integer from 1 to 120000');
    if (!Number.isInteger(maxResponseBytes) || maxResponseBytes < 1 || maxResponseBytes > 32 * 1024 * 1024)
      throw new TypeError('maxResponseBytes must be between 1 and 33554432');
    if (typeof fetchImpl !== 'function') throw new TypeError('fetch is required');
    this.#token = token;
    this.#fetch = fetchImpl;
    this.#timeout = timeoutMs;
    this.#maximum = maxResponseBytes;
  }
  static isReadOnly(tool) {
    if (!READ_ONLY.has(tool)) throw new TypeError('Unknown DeedKit tool');
    return READ_ONLY.get(tool);
  }
  async call(tool, arguments_ = {}, {signal} = {}) {
    if (typeof tool !== 'string' || !READ_ONLY.has(tool)) throw new TypeError('Unknown DeedKit tool');
    if (!plainObject(arguments_) || Object.hasOwn(arguments_, 'token') || !finiteJson(arguments_))
      throw new TypeError('Arguments must be a JSON object without a token field');
    if (signal !== undefined && !(signal instanceof AbortSignal)) throw new TypeError('Invalid signal');
    const body = JSON.stringify(arguments_);
    const maximum = tool === UPLOAD_CHUNK_TOOL ? UPLOAD_REQUEST_BYTES : REQUEST_BYTES;
    if (Buffer.byteLength(body) > maximum) throw new TypeError(`Request exceeds ${maximum} bytes`);
    if (signal?.aborted) throw new DeedKitError('request_aborted', {tool});
    const readOnly = READ_ONLY.get(tool);
    // Without a definitive gateway answer a mutation may already have happened.
    const undecided = error => readOnly ? error : new DeedKitError(error.code, {status: error.status, tool,
      outcome: 'unknown'});
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.#timeout);
    const activeSignal = signal ? AbortSignal.any([signal, controller.signal]) : controller.signal;
    let response;
    try {
      try {
        response = await this.#fetch(new URL(`v1/${tool}`, this.#base), {
          method: 'POST', redirect: 'error', signal: activeSignal, body,
          headers: {'Authorization': `Bearer ${this.#token}`, 'Content-Type': 'application/json',
            'Accept': 'application/json'},
        });
      } catch {
        // Do not expose URLs, auth headers or transport exception text.
        throw undecided(new DeedKitError(activeSignal.aborted ? 'request_aborted' : 'transport_error', {tool}));
      }
      if (response.status >= 300 && response.status < 400) {
        await response.body?.cancel();
        throw undecided(new DeedKitError('redirect_refused', {status: response.status, tool}));
      }
      let value;
      try { value = await boundedJson(response, this.#maximum); } catch (error) {
        throw undecided(error instanceof DeedKitError ? new DeedKitError(error.code, {status: response.status, tool})
          : new DeedKitError(activeSignal.aborted ? 'request_aborted' : 'transport_error', {status: response.status, tool}));
      }
      if (!response.ok) throw gatewayError(tool, response.status, value);
      return value;
    } finally { clearTimeout(timer); }
  }
  platformCapabilities(options) { return this.call('platform_capabilities', {}, options); }
  async downloadFile(grant, output, {signal} = {}) {
    // A ticket from messages_get_attachment is itself the bearer credential; the
    // gateway key is never sent to the download URL.
    if (!plainObject(grant) || typeof grant.download_url !== 'string' ||
        !Number.isSafeInteger(grant.bytes) || grant.bytes < 0 ||
        typeof grant.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(grant.sha256))
      throw new TypeError('A messages_get_attachment result with download_url, bytes and sha256 is required');
    if (typeof output !== 'string' || !output || output.includes('\x00')) throw new TypeError('Invalid output path');
    if (signal !== undefined && !(signal instanceof AbortSignal)) throw new TypeError('Invalid signal');
    let url;
    try { url = new URL(grant.download_url); } catch { throw new DeedKitError('invalid_download_url'); }
    const prefix = this.#base.pathname;
    if (url.origin !== this.#base.origin || url.username || url.password || url.search || url.hash ||
        !url.pathname.startsWith(prefix) ||
        !/^(?:_releases\/[0-9a-f]{12}\/)?v1\/files\/[A-Za-z0-9_-]{43}$/.test(url.pathname.slice(prefix.length)))
      throw new DeedKitError('invalid_download_url');
    if (signal?.aborted) throw new DeedKitError('request_aborted');
    const handle = await open(output, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL |
      (constants.O_NOFOLLOW ?? 0), 0o600).catch(() => { throw new DeedKitError('output_unavailable'); });
    const controller = new AbortController();
    let timer;
    // The deadline covers each wait for the server, not the whole transfer.
    const arm = () => { clearTimeout(timer); timer = setTimeout(() => controller.abort(), this.#timeout); };
    const activeSignal = signal ? AbortSignal.any([signal, controller.signal]) : controller.signal;
    let complete = false;
    try {
      arm();
      let response;
      try {
        response = await this.#fetch(url, {method: 'GET', redirect: 'error', signal: activeSignal,
          headers: {'Accept': 'application/octet-stream', 'Accept-Encoding': 'identity'}});
      } catch { throw new DeedKitError(activeSignal.aborted ? 'request_aborted' : 'transport_error'); }
      if (response.status !== 200) {
        if (response.status >= 300 && response.status < 400) {
          await response.body?.cancel();
          throw new DeedKitError('redirect_refused', {status: response.status});
        }
        let value;
        try { value = await boundedJson(response, 65536); } catch {
          throw new DeedKitError('http_error', {status: response.status});
        }
        throw gatewayError(null, response.status, value);
      }
      const length = response.headers.get('content-length');
      if (length !== null && length !== String(grant.bytes)) {
        await response.body?.cancel();
        throw new DeedKitError('download_integrity_error', {status: response.status});
      }
      const digest = createHash('sha256');
      let total = 0;
      try {
        for await (const chunk of response.body ?? []) {
          arm();
          total += chunk.byteLength;
          if (total > grant.bytes) throw new DeedKitError('download_integrity_error', {status: 200});
          digest.update(chunk);
          await handle.write(chunk);
        }
      } catch (error) {
        if (error instanceof DeedKitError) throw error;
        throw new DeedKitError(activeSignal.aborted ? 'request_aborted' : 'transport_error', {status: 200});
      }
      if (total !== grant.bytes || digest.digest('hex') !== grant.sha256)
        throw new DeedKitError('download_integrity_error', {status: 200});
      await handle.sync();
      complete = true;
      return {path: output, bytes: total, sha256: grant.sha256};
    } finally {
      clearTimeout(timer);
      await handle.close().catch(() => {});
      // Never leave a partial or unverified file behind under the requested name.
      if (!complete) await unlink(output).catch(() => {});
    }
  }
  async downloadAttachment(args, output, options = {}) {
    const grant = await this.call('messages_get_attachment', args, options);
    return {...await this.downloadFile(grant, output, options), filename: typeof grant.filename === 'string'
      ? grant.filename : null};
  }
}
