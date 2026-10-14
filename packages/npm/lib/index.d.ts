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
}
