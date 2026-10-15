-- Provider-verified namespace links are metadata, not message identity rewrites.
CREATE FUNCTION messenger_whatsapp_personal_id(value text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT CASE WHEN value ~ '^[0-9]{1,20}(:[0-9]{1,5})?@(c[.]us|lid)$'
    THEN regexp_replace(value, ':[0-9]+@', '@') ELSE NULL END
$$;

CREATE TABLE whatsapp_identity_observations (
    account text NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    source_message text NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    identity_id text NOT NULL,
    pn text NOT NULL CHECK(pn ~ '^[0-9]{1,20}@c[.]us$'),
    lid text NOT NULL CHECK(lid ~ '^[0-9]{1,20}@lid$'),
    provenance text NOT NULL CHECK(provenance='webjs_contact_cache_v1'),
    first_observed_at timestamptz NOT NULL,
    observed_at timestamptz NOT NULL,
    CHECK(messenger_whatsapp_personal_id(identity_id) IS NOT NULL AND
      messenger_whatsapp_personal_id(identity_id) IN (pn,lid)),
    CHECK(first_observed_at<=observed_at),
    PRIMARY KEY(account,source_message,identity_id,pn,lid,provenance)
);
CREATE INDEX whatsapp_identity_pn ON whatsapp_identity_observations(account,pn,lid);
CREATE INDEX whatsapp_identity_lid ON whatsapp_identity_observations(account,lid,pn);
CREATE INDEX whatsapp_identity_source ON whatsapp_identity_observations(source_message);
CREATE INDEX whatsapp_identity_personal ON whatsapp_identity_observations
    (account,messenger_whatsapp_personal_id(identity_id),observed_at DESC,source_message);
