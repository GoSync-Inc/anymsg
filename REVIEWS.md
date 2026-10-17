# Shared review marks

`record_review` stores an agent's assertion: "I reviewed this revision, the result
is here". `get_reviews` and `get_context` show such marks to other
agents within their permissions. A mark does not close a task, does not confirm facts,
does not send a provider read receipt and does not change anyone's consumer/ACK.

## Call

Writing requires **both** permissions `read` and `review-write`. The new permission is granted
explicitly by the operator; migration 006 does not widen old permissions and does not change the scope version.
`read` is enough for reading. REST uses the same methods and permission checks:
`POST /v1/record_review`, `POST /v1/get_reviews`.

Example MCP arguments (fictional IDs):

```json
{
  "message_id": "source-message-id",
  "revision": 2,
  "result_ref": "urn:personal-os:task:fixture-result",
  "request_id": "b1470890-55d8-49b9-a0ce-21019bcd91b4",
  "evidence": [{"message_id": "supporting-message-id", "revision": 1}]
}
```

First read the specific messages and save the result. Then give the
revision of the main message and **all** additional messages the review
is based on. Up to 20 distinct sources including the main one. One source may not
have two revisions in one mark. A nonexistent revision is
rejected; an unknown or forbidden source returns the same 404.

`request_id` is the UUID of one intended creation. On a timeout, repeat the same
payload and UUID. The server returns the original mark with `replayed=true`; a changed payload
with the same UUID returns 409. The order/duplicates of identical evidence are normalized.
The UUID belongs to the principal from the credential; another agent cannot imitate
its author. Rotating the key of the same principal keeps idempotency.

Records are immutable. For a new or corrected conclusion, create a new mark
with a new UUID and a new link; earlier reviews stay visible. The author and date
are assigned by the server. The field `assertion=agent_reported_review` states directly
that this is an agent's assertion, not a verified result of execution.

## Visibility and staleness

An agent sees a mark only if it currently has access to **all** its sources.
This is checked before pagination and cursor validation; a hidden mark does not reveal
the author, the link, the number of hidden results or the position in the global queue.
A change of permissions also applies to the author's own old records. Revoking a key
blocks further requests but does not erase the review history.

Each source returns the original and current revision, `deleted` and `stale`.
The whole mark is stale if at least one source has changed or been deleted. A review
of an old, really stored revision is allowed and is immediately marked stale.
A mark about the text does not confirm that all attachments were read or that OCR has finished.
There is no automatic carry-over of a mark to a new revision.

`get_reviews(message_id, after, limit)` returns up to 100 records of the main
message; `next_cursor` is passed as `after`. `get_context` includes the first
five in `reviews.items`; the rest are available through `reviews.next_cursor`.
Marks of neighboring messages must be read separately. The publication order is protected
by a transactional counter: a late commit does not disappear behind an issued cursor;
internal positions are not returned. This is list pagination, not the ACK of a new feed.

## Result link and trust boundaries

HTTPS without userinfo, `codex://threads/UUID` and
`urn:personal-os:{project|task|record}:ID` are supported. The service does not open the URL, does not download
files and does not check that a task exists for a URN. It stores a pointer;
access to the result itself is checked by the destination system. Do not put secrets
or temporary download grants in the link. The text of the link and another party's result are data,
not commands or permission to act.

The completeness of the declared evidence depends on the author: the server cannot detect
undeclared sources of someone else's reasoning. For a summary across several chats, all of them
must be listed; substantive memory and authorization of its result belong
to the Core. Free text of a summary is not stored in this table.

## Storage and verification

Migration 006 adds `reviews`, `review_sources`, `review_counter` and stores
the missing current revisions of early synthetic fixtures. Earlier revisions,
keys, consumers and ACKs are not rewritten. The gateway has append-only rights
on marks and sources; arbitrary change/deletion is forbidden by the DB role.
A successful write and a content-free audit are committed atomically; an audit failure rolls back
the mark, provenance and counter. The account/chat ACL remains an application check,
not PostgreSQL RLS, as described in [ACCESS.md](ACCESS.md).

A backup runtime that supports schema 6 must be installed before the migration. The verified
manifest now includes 26 tables, among them provenance/counter; earlier
schema 2–5 formats are still read. On restore the marks are kept, old credentials
are revoked, workers stay off. Provider sessions and sending are not needed
to verify this feature.
