-- Operator-only installation after schema26. No provider sessions, token hashes,
-- message mutation, key administration or outbound send permissions.
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='messaging_subscriptions') THEN
        CREATE ROLE messaging_subscriptions NOLOGIN;
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO messaging_subscriptions;
GRANT SELECT(id,accounts,chat_ids,permissions,enabled,scope_version)
    ON principals TO messaging_subscriptions;
GRANT SELECT(id,principal,revoked,expires_at,permissions,permission_version)
    ON api_keys TO messaging_subscriptions;
GRANT SELECT ON events,feed_counter,subscription_sources TO messaging_subscriptions;
GRANT SELECT ON subscriptions TO messaging_subscriptions;
GRANT UPDATE(state,version,updated_at,matched_through,blocked_reason,gap,
             missing_evidence,last_matched_at) ON subscriptions TO messaging_subscriptions;
GRANT SELECT,INSERT ON subscription_matches TO messaging_subscriptions;
