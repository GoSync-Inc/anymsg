-- Multi-account history cannot obtain global date order from an index whose
-- first key is account. This avoids sorting the full archive for LIMIT pages.
CREATE INDEX message_live_date ON messages(sent_at,id) WHERE NOT deleted;
-- Exact sender lookup, including a sender with no matches, should not require
-- walking the date index across all messages in permitted accounts.
CREATE INDEX message_live_sender_date ON messages(account,sender,sent_at,id) WHERE NOT deleted;
