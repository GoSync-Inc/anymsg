const METHODS = Object.freeze([
  'capabilities', 'list_chats', 'search', 'search_people', 'search_files',
  'get_messages', 'get_context', 'get_sync_status', 'resolve_reference',
  'get_attachment', 'get_file_text', 'get_file_segments', 'get_file_occurrences',
  'get_updates', 'ack_updates', 'get_reviews', 'record_review',
  'preview_message','send_message','get_send_status',
]);
export const tools = METHODS;
const SAFE_ERRORS = new Set([
  'ambiguous_reference', 'attachment_unavailable', 'body_timeout', 'body_too_large',
  'chat_required', 'consumer_filter_mismatch', 'consumer_limit', 'cursor_changed',
  'disconnected', 'forbidden', 'invalid_body', 'invalid_consumer', 'invalid_cursor',
  'invalid_date_range', 'invalid_filter', 'invalid_fuzzy_query', 'invalid_host',
  'invalid_limit', 'invalid_origin', 'invalid_parameters', 'invalid_query',
  'invalid_receipt', 'invalid_utc_date', 'invalid_version', 'not_found',
  'query_timeout', 'rate_limited', 'service_busy', 'service_unavailable',
  'stale_ack', 'temporarily_unavailable', 'unauthorized', 'version_conflict',
  'invalid_message_text','invalid_request_id','idempotency_conflict','sending_disabled','sender_not_ready',
  'invalid_message_format','unsafe_message_markup','invalid_message_entities',
  'formatting_too_complex','unsupported_message_formatting',
  'lossy_formatting_requires_confirmation','message_too_long','preview_required','preview_mismatch',
]);

export class AnyMsgError extends Error {
  constructor(code, {status = 0, requestId = null, retryable = false} = {}) {
    super(`AnyMsg request failed: ${code}`);
    this.name = 'AnyMsgError';
    this.code = code;
    this.status = status;
    this.requestId = requestId;
    this.retryable = retryable;
  }
}

function base(value) {
  let url;
  try { url = new URL(value); } catch { throw new TypeError('Invalid AnyMsg base URL'); }
  if (url.username || url.password || url.search || url.hash)
    throw new TypeError('Base URL must not contain credentials, query or fragment');
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && local))
    throw new TypeError('Use HTTPS, or HTTP on loopback for local development');
  url.pathname = url.pathname.replace(/\/+$/, '') + '/';
  if (/\/(mcp|v1)\/$/.test(url.pathname))
    throw new TypeError('Base URL is the server root, without /mcp or /v1');
  return url;
}

async function boundedJson(response, maximum) {
  const length = response.headers.get('content-length');
  if (length && /^\d+$/.test(length) && Number(length) > maximum) {
    await response.body?.cancel();
    throw new AnyMsgError('response_too_large', {status: response.status});
  }
  if (!response.headers.get('content-type')?.toLowerCase().includes('application/json')) {
    await response.body?.cancel();
    throw new AnyMsgError('invalid_response', {status: response.status});
  }
  const reader = response.body?.getReader();
  if (!reader) throw new AnyMsgError('invalid_response', {status: response.status});
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maximum) {
        await reader.cancel();
        throw new AnyMsgError('response_too_large', {status: response.status});
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  try {
    const value = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
    return value;
  } catch { throw new AnyMsgError('invalid_response', {status: response.status}); }
}

export class AnyMsgClient {
  #base;
  #token;
  #fetch;
  #timeout;
  #maximum;
  constructor({baseUrl, token, timeoutMs = 30000, maxResponseBytes = 4 * 1024 * 1024,
    fetch: fetchImpl = globalThis.fetch} = {}) {
    this.#base = base(baseUrl);
    if (typeof token !== 'string' || !/^[!-~]{1,4096}$/.test(token))
      throw new TypeError('A non-empty Bearer token is required');
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
  async call(method, arguments_ = {}, {signal} = {}) {
    if (!METHODS.includes(method)) throw new TypeError('Unknown AnyMsg operation');
    if (!arguments_ || typeof arguments_ !== 'object' || Array.isArray(arguments_) || 'token' in arguments_)
      throw new TypeError('Arguments must be an object without a token field');
    let body;
    try { body = JSON.stringify(arguments_); } catch { throw new TypeError('Arguments must be JSON serializable'); }
    if (!body || Buffer.byteLength(body) > 16384) throw new TypeError('Request exceeds 16384 bytes');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.#timeout);
    const activeSignal = signal ? AbortSignal.any([signal, controller.signal]) : controller.signal;
    try {
      const response = await this.#fetch(new URL(`v1/${method}`, this.#base), {
        method: 'POST', redirect: 'error', signal: activeSignal,
        headers: {'Authorization': `Bearer ${this.#token}`, 'Content-Type': 'application/json', 'Accept': 'application/json'}, body,
      });
      if (response.status >= 300 && response.status < 400) {
        await response.body?.cancel();
        throw new AnyMsgError('redirect_refused', {status: response.status});
      }
      const value = await boundedJson(response, this.#maximum);
      if (!response.ok) {
        const code = typeof value.error === 'string' && SAFE_ERRORS.has(value.error)
          ? value.error : 'http_error';
        const requestId = typeof value.request_id === 'string' && /^[0-9a-f]{32}$/.test(value.request_id)
          ? value.request_id : null;
        throw new AnyMsgError(code, {status: response.status, requestId, retryable: value.retryable === true});
      }
      return value;
    } catch (error) {
      if (error instanceof AnyMsgError) throw error;
      // Do not expose URLs, auth headers or arbitrary server error text.
      throw new AnyMsgError(activeSignal.aborted ? 'request_aborted' : 'transport_error');
    } finally { clearTimeout(timer); }
  }
  capabilities(args = {}) { return this.call('capabilities', args); }
  listChats(args = {}) { return this.call('list_chats', args); }
  search(args = {}) { return this.call('search', args); }
  searchPeople(args = {}) { return this.call('search_people', args); }
  searchFiles(args = {}) { return this.call('search_files', args); }
  getMessages(args) { return this.call('get_messages', args); }
  getContext(args) { return this.call('get_context', args); }
  getSyncStatus(args = {}) { return this.call('get_sync_status', args); }
  resolveReference(args) { return this.call('resolve_reference', args); }
  getAttachment(args) { return this.call('get_attachment', args); }
  getFileText(args) { return this.call('get_file_text', args); }
  getFileSegments(args) { return this.call('get_file_segments', args); }
  getFileOccurrences(args) { return this.call('get_file_occurrences', args); }
  getUpdates(args = {}) { return this.call('get_updates', args); }
  ackUpdates(args) { return this.call('ack_updates', args); }
  getReviews(args) { return this.call('get_reviews', args); }
  recordReview(args) { return this.call('record_review', args); }
  previewMessage(args) { return this.call('preview_message',args); }
  sendMessage(args) { return this.call('send_message',args); }
  getSendStatus(args) { return this.call('get_send_status',args); }
}
