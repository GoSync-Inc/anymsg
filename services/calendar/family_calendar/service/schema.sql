CREATE TABLE people(id text PRIMARY KEY, label text NOT NULL);
CREATE TABLE groups(id text PRIMARY KEY, label text NOT NULL, version bigint NOT NULL DEFAULT 1);
CREATE TABLE memberships(group_id text REFERENCES groups, person_id text REFERENCES people,
 role text NOT NULL CHECK(role IN ('group_admin','coordinator','adult','dependent','helper')),
 enabled boolean NOT NULL DEFAULT true, version bigint NOT NULL DEFAULT 1, PRIMARY KEY(group_id,person_id));
CREATE TABLE guardians(group_id text, adult_id text, dependent_id text, PRIMARY KEY(group_id,adult_id,dependent_id),
 FOREIGN KEY(group_id,adult_id) REFERENCES memberships(group_id,person_id),
 FOREIGN KEY(group_id,dependent_id) REFERENCES memberships(group_id,person_id));
CREATE TABLE connections(id text PRIMARY KEY, owner_id text REFERENCES people NOT NULL,
 provider text NOT NULL CHECK(provider IN ('google','icloud')), enabled boolean NOT NULL DEFAULT true,
 version bigint NOT NULL DEFAULT 1);
CREATE TABLE calendars(ref text PRIMARY KEY, connection_id text REFERENCES connections NOT NULL,
 target text NOT NULL, label text NOT NULL, timezone text NOT NULL, privilege text NOT NULL,
 write_enabled boolean NOT NULL DEFAULT false, version bigint NOT NULL DEFAULT 1,
 UNIQUE(connection_id,target));
CREATE TABLE calendar_grants(group_id text, person_id text, calendar_ref text REFERENCES calendars,
 scopes jsonb NOT NULL, visibility text NOT NULL CHECK(visibility IN ('details','busy_only')),
 version bigint NOT NULL DEFAULT 1, PRIMARY KEY(group_id,person_id,calendar_ref),
 FOREIGN KEY(group_id,person_id) REFERENCES memberships(group_id,person_id));
CREATE TABLE bindings(group_id text, person_id text, calendar_ref text REFERENCES calendars,
 PRIMARY KEY(group_id,person_id,calendar_ref),
 FOREIGN KEY(group_id,person_id) REFERENCES memberships(group_id,person_id));
CREATE TABLE principals(id text PRIMARY KEY, group_id text, person_id text, scopes jsonb NOT NULL,
 key_hash text UNIQUE NOT NULL, enabled boolean NOT NULL DEFAULT true, version bigint NOT NULL DEFAULT 1,
 expires_at timestamptz NOT NULL DEFAULT (now()+interval '90 days'),
 FOREIGN KEY(group_id,person_id) REFERENCES memberships(group_id,person_id));
CREATE TABLE agent_calendars(principal_id text REFERENCES principals, calendar_ref text REFERENCES calendars,
 visibility text NOT NULL CHECK(visibility IN ('details','busy_only')), PRIMARY KEY(principal_id,calendar_ref));
CREATE TABLE resources(ref text PRIMARY KEY, calendar_ref text REFERENCES calendars NOT NULL,
 target text NOT NULL, etag text, payload jsonb NOT NULL, UNIQUE(calendar_ref,target));
CREATE TABLE occurrences(ref text PRIMARY KEY, resource_ref text REFERENCES resources NOT NULL,
 calendar_ref text REFERENCES calendars NOT NULL, start_at timestamptz NOT NULL, end_at timestamptz NOT NULL,
 details jsonb NOT NULL, busy boolean NOT NULL, CHECK(end_at>start_at));
CREATE INDEX occurrences_window ON occurrences(calendar_ref,start_at,end_at);
CREATE TABLE coverage(calendar_ref text PRIMARY KEY REFERENCES calendars, start_at timestamptz NOT NULL,
 end_at timestamptz NOT NULL, status text NOT NULL, checked_at timestamptz NOT NULL DEFAULT now(), error text);
CREATE TABLE sync_jobs(id text PRIMARY KEY, principal_id text REFERENCES principals NOT NULL,
 calendar_ref text REFERENCES calendars NOT NULL, start_at timestamptz NOT NULL, end_at timestamptz NOT NULL,
 state text NOT NULL DEFAULT 'prepared', created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE plans(id text PRIMARY KEY, principal_id text REFERENCES principals NOT NULL, payload jsonb NOT NULL,
 scope_hash text NOT NULL, expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,principal_id));
CREATE TABLE operations(id text PRIMARY KEY, principal_id text REFERENCES principals NOT NULL, plan_id text NOT NULL,
 request_id text NOT NULL, state text NOT NULL DEFAULT 'queued', lease_until timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), error text,
 UNIQUE(principal_id,request_id), FOREIGN KEY(plan_id,principal_id) REFERENCES plans(id,principal_id));
CREATE TABLE operation_steps(operation_id text REFERENCES operations NOT NULL, ordinal int NOT NULL,
 state text NOT NULL DEFAULT 'prepared', intent jsonb, receipt jsonb, error text,
 PRIMARY KEY(operation_id,ordinal));
CREATE TABLE assignments(id text PRIMARY KEY, group_id text, person_id text, principal_id text REFERENCES principals,
 duty text NOT NULL CHECK(duty IN ('dropoff','pickup','stay_with_child')),
 start_at timestamptz NOT NULL, end_at timestamptz NOT NULL,
 status text NOT NULL CHECK(status IN ('proposed','assigned','declined','cancelled')),
 version bigint NOT NULL DEFAULT 1, CHECK(end_at>start_at),
 FOREIGN KEY(group_id,person_id) REFERENCES memberships(group_id,person_id));
CREATE TABLE audit(id bigserial PRIMARY KEY, principal_id text, action text NOT NULL, ref text,
 outcome text NOT NULL, at timestamptz NOT NULL DEFAULT now());
