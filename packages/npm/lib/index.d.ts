export type Json = null | boolean | number | string | Json[] | {[key: string]: Json};
export type Arguments = {[key: string]: Json};
export type MessageFormat = 'plain' | 'markdown' | 'html';
export interface MessageDraft {
  account: string;
  chat_id: string;
  text: string;
  reply_to?: string;
  format?: MessageFormat;
  link_preview?: boolean;
  allow_lossy?: boolean;
}
export interface MessageAction extends MessageDraft {
  request_id: string;
  preview_digest?: string;
}
export interface MessengerOperationPreview {
  account: string;
  operation: string;
  chat_id?: string;
  message_id?: string;
  /** Exact archived message revision; required by the selected message action. */
  expected_revision?: number;
  /** Strict allowlisted parameters from listMessengerOperations, never a raw RPC payload. */
  parameters?: Arguments;
}
export interface MessengerOperationAction extends MessengerOperationPreview {
  request_id: string;
  /** Required for mutations; binds payload but never proves owner approval. */
  preview_digest?: string;
}
export interface FileUploadPreparation {
  account: string;
  chat_id: string;
  /** Basename only, no URL or server path. */
  filename: string;
  /** MIME type without parameters, e.g. image/jpeg. */
  mime: string;
  /** 1–33554432 bytes. */
  bytes: number;
  /** Lowercase SHA-256 of the exact original bytes. */
  sha256: string;
  /** Stable across explicit reconciliation/resume; never generate another ID after uncertainty. */
  request_id: string;
}
export interface FileUploadChunk {
  upload_id: string;
  offset: number;
  /** Canonical padded base64 for 1–262144 raw bytes. */
  data_base64: string;
}
export interface FileUploadStatus {
  upload_id: string;
  account: string;
  chat_id: string;
  filename: string;
  mime: string;
  bytes: number;
  sha256: string;
  offset: number;
  status: 'uploading' | 'ready' | 'expired';
  chunk_bytes: number;
  expires_at: string;
  changes_provider_state: false;
  owner_approval_verified_by_server: false;
}
export interface LocalFileUploadOptions {
  account: string;
  chat_id: string;
  request_id: string;
  /** Defaults to the local basename. */
  filename?: string;
  /** Defaults to application/octet-stream; use image/jpeg or image/png for photos. */
  mime?: string;
  signal?: AbortSignal;
}
export type SubscriptionEventKind = 'message.created' | 'message.edited' | 'message.deleted' |
  'message.status' | 'attachment.ready' | 'file.text.ready' | 'file.transcript.ready' |
  'file.description.ready' | 'file.processing.failed';
export type SubscriptionOrigin = 'live' | 'backfill' | 'reconcile' | 'fixture';
export interface SubscriptionSelection {
  /** Frozen 1–64 authorized archive chat IDs, never names or arbitrary provider references. */
  chat_ids: string[];
  /** Exact verified account-scoped IDs in those chats, resolved through searchPeople. */
  sender_ids?: string[] | null;
  /** Defaults to message.created; file readiness is independent of message arrival. */
  kinds?: SubscriptionEventKind[] | null;
  direction?: '' | 'incoming' | 'outgoing';
  /** Defaults to live; historical origins require explicit selection. */
  origins?: SubscriptionOrigin[] | null;
  /** Correlation only, not authority to schedule another task. */
  label?: string;
  /** Future zoned ISO timestamp, at most 366 days away. */
  expires_at?: string | null;
}
export interface SubscriptionLifecycleAction {
  subscription_id: string;
  version: number;
  action: 'pause' | 'resume' | 'close';
  resume_policy?: 'catch_up' | 'skip';
}
export declare const tools: readonly string[];
export declare class AnyMsgError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string | null;
  readonly retryable: boolean;
}
export declare class AnyMsgClient {
  constructor(options: {baseUrl: string; token: string; timeoutMs?: number;
    maxResponseBytes?: number; fetch?: typeof globalThis.fetch});
  call<T = Record<string, unknown>>(method: string, arguments_?: Arguments, options?: {signal?: AbortSignal}): Promise<T>;
  capabilities(args?: Arguments): Promise<Record<string, unknown>>;
  prepareFileUpload(args: FileUploadPreparation, options?: {signal?: AbortSignal}): Promise<FileUploadStatus>;
  uploadFileChunk(args: FileUploadChunk, options?: {signal?: AbortSignal}): Promise<FileUploadStatus>;
  getFileUpload(args: {upload_id:string}, options?: {signal?: AbortSignal}): Promise<FileUploadStatus>;
  /** Stream a local regular file, reject changes, and stage only. Never previews or sends. */
  uploadFile(file: string, options: LocalFileUploadOptions): Promise<FileUploadStatus>;
  dismissReply(args: {chat_id: string; message_id: string; expected_revision: number}): Promise<Record<string, unknown>>;
  listChats(args?: Arguments): Promise<Record<string, unknown>>;
  search(args?: Arguments): Promise<Record<string, unknown>>;
  searchPeople(args?: Arguments): Promise<Record<string, unknown>>;
  searchFiles(args?: Arguments): Promise<Record<string, unknown>>;
  getMessages(args: Arguments): Promise<Record<string, unknown>>;
  getContext(args: Arguments): Promise<Record<string, unknown>>;
  getSyncStatus(args?: Arguments): Promise<Record<string, unknown>>;
  resolveReference(args: Arguments): Promise<Record<string, unknown>>;
  getAttachment(args: Arguments): Promise<Record<string, unknown>>;
  getFileText(args: Arguments): Promise<Record<string, unknown>>;
  getFileSegments(args: Arguments): Promise<Record<string, unknown>>;
  getFileOccurrences(args: Arguments): Promise<Record<string, unknown>>;
  getUpdates(args?: Arguments): Promise<Record<string, unknown>>;
  ackUpdates(args: Arguments): Promise<Record<string, unknown>>;
  getReviews(args: Arguments): Promise<Record<string, unknown>>;
  recordReview(args: Arguments): Promise<Record<string, unknown>>;
  /** Passive rendering. Its digest binds the payload but does not prove owner approval. */
  previewMessage(args: MessageDraft): Promise<Record<string,unknown>>;
  /** Rich content requires the reviewed preview digest and exact owner confirmation. */
  sendMessage(args: MessageAction): Promise<Record<string,unknown>>;
  getSendStatus(args: {send_id:string}): Promise<Record<string,unknown>>;
  /** Passive catalog and scoped permission read, with no provider request. */
  listMessengerOperations(args: {account:string}): Promise<Record<string,unknown>>;
  /** Passive validation; show exact targets, effects and warnings before owner confirmation. */
  previewMessengerOperation(args: MessengerOperationPreview): Promise<Record<string,unknown>>;
  /** Separate live-operation permissions and account opt-in; never retries submitted/unknown effects. */
  executeMessengerOperation(args: MessengerOperationAction): Promise<Record<string,unknown>>;
  /** Read a scoped receipt without another provider request. */
  getMessengerOperation(args: {operation_id:string}): Promise<Record<string,unknown>>;
  /** Requires explicit subscription-write; uncertain creations are never automatically retried. */
  createSubscription(args: SubscriptionSelection): Promise<Record<string,unknown>>;
  /** Current credential only; sources are masked after a permission/scope change. */
  listSubscriptions(args?: {limit?:number}): Promise<Record<string,unknown>>;
  /** Matcher catch-up is separate from live provider freshness and complete history. */
  getSubscription(args: {subscription_id:string}): Promise<Record<string,unknown>>;
  /** Version-checked local lifecycle operation; no messenger action or agent wake-up. */
  updateSubscription(args: SubscriptionLifecycleAction): Promise<Record<string,unknown>>;
  /** Passive matched-reference inspection. No durable consumer ACK or push in this release. */
  getSubscriptionEvents(args: {subscription_id:string; after?:number; limit?:number}): Promise<Record<string,unknown>>;
}
