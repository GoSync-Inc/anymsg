# People search

`search_people(query, accounts, chats, after, limit)` searches by observed name,
username (with or without `@`) and provider sender ID. The search is case-insensitive
and matches a literal fragment; `%` and `_` are not wildcards. For fragments
of three or more characters a PostgreSQL trigram GIN is used. The page limit is 100.

The result contains `account`, `chat`, `sender_id`, `display_name`, `username`,
`source_message`, the message date and the observation time. One sender may
appear in several chats as separate rows. These are provider hints,
not confirmation of identity; identical names/IDs of different accounts are not merged.

For the next step, pass `account` + `sender_id` to `search` (also `chat` if needed),
or `source_message` to `get_context`. `next_cursor` is used
as `after`. Account/chat/read permissions are checked on every call, including the cursor.
A name from a closed chat does not appear in the results of an open one. If the message that was the source
of a name is deleted, the corresponding observation is hidden until the next suitable one.

Telegram uses the cached sender entity from the received message, WhatsApp uses the
name from the message payload, if available. Capture makes no extra
network lookup and does not import the whole address book. Therefore old records without
this metadata, unknown usernames or unavailable contacts cannot be found by
name; use the exact sender ID in `search`. Checking the completeness of the catalog on
working accounts remains part of live acceptance.

A name update does not create a new revision of the message text. An old backfill,
a conflicting event or a deletion does not replace the latest name observation.
The operation reads the archive without provider read receipts and without advancing the agent ACK.
