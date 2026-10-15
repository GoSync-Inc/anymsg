-- Keep the original simple vector for literal identifiers and old queries.
-- Built-in configurations are required explicitly; do not silently pretend a
-- missing language is stemmed. Stock PostgreSQL 17 includes Indonesian.
DO $$ BEGIN
    PERFORM 'pg_catalog.russian'::regconfig;
    PERFORM 'pg_catalog.english'::regconfig;
    PERFORM 'pg_catalog.indonesian'::regconfig;
END $$;
ALTER TABLE messages ADD COLUMN document_russian tsvector
    GENERATED ALWAYS AS (to_tsvector('pg_catalog.russian',body)) STORED;
ALTER TABLE messages ADD COLUMN document_english tsvector
    GENERATED ALWAYS AS (to_tsvector('pg_catalog.english',body)) STORED;
ALTER TABLE messages ADD COLUMN document_indonesian tsvector
    GENERATED ALWAYS AS (to_tsvector('pg_catalog.indonesian',body)) STORED;
CREATE INDEX message_fts_russian ON messages USING gin(document_russian) WHERE NOT deleted;
CREATE INDEX message_fts_english ON messages USING gin(document_english) WHERE NOT deleted;
CREATE INDEX message_fts_indonesian ON messages USING gin(document_indonesian) WHERE NOT deleted;
CREATE INDEX message_body_trigram ON messages USING gin(lower(body) gin_trgm_ops) WHERE NOT deleted;
ALTER TABLE chats ADD COLUMN chat_type text CHECK(chat_type IN ('private','group','channel','unknown'));
-- Existing consumers retain the empty filter. A different filter requires a
-- new consumer name; never retroactively reinterpret an outstanding ACK.
ALTER TABLE consumers ADD COLUMN filter_spec jsonb NOT NULL DEFAULT '{}';

-- Deterministic spelling candidates only, never a verified identity link.
CREATE FUNCTION messenger_transliterate(value text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT coalesce(string_agg(coalesce(
        '{"а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"yo","ж":"zh","з":"z","и":"i","й":"y","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f","х":"kh","ц":"ts","ч":"ch","ш":"sh","щ":"shch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya"}'::jsonb ->> ch,ch),'' ORDER BY ord),'')
    FROM regexp_split_to_table(lower(value),'') WITH ORDINALITY AS letters(ch,ord)
$$;
CREATE INDEX sender_profile_transliteration ON sender_profiles
    USING gin(messenger_transliterate(search_text) gin_trgm_ops);
