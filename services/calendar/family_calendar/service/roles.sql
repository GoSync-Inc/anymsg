-- Applied by owner after migration, with separately generated login passwords.
GRANT USAGE ON SCHEMA fcs TO fcs_gateway, fcs_worker;
GRANT SELECT ON ALL TABLES IN SCHEMA fcs TO fcs_gateway, fcs_worker;
GRANT INSERT ON fcs.plans, fcs.operations, fcs.operation_steps, fcs.sync_jobs, fcs.assignments, fcs.audit TO fcs_gateway;
GRANT UPDATE ON fcs.assignments TO fcs_gateway;
GRANT INSERT,UPDATE,DELETE ON fcs.resources, fcs.occurrences, fcs.coverage TO fcs_worker;
GRANT INSERT ON fcs.audit TO fcs_worker;
GRANT UPDATE ON fcs.operations, fcs.operation_steps, fcs.sync_jobs TO fcs_worker;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA fcs TO fcs_gateway, fcs_worker;
-- Neither process can issue/revoke keys, change grants, memberships, providers or write opt-in.
-- Provider secrets and raw encrypted source payload keys are outside PostgreSQL, worker-only.
