export type Json = null | boolean | number | string | Json[] | {[key: string]: Json};
export type Arguments = {[key: string]: Json};
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
}
