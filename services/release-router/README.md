# Internal HTTP release router

This small, standard-library Go process owns only `/deedkit/*` and
`/pos-probe/*` behind the existing public ingress. Its HTTP listener stays open
when an application generation changes. It does not terminate TLS, control
provider sessions, migrate databases or update itself during a release.

Routing selection and request-lease acquisition share the configuration mutex.
A lease starts before dialing an upstream and ends when `ReverseProxy` returns,
including upload, response streaming, errors and client cancellation. A request
whose headers finish after a configuration switch selects the new generation.
The original Host, authorization and ingress forwarding headers are preserved.

Private HTTP control uses a Unix socket with mode `0600` in a process-owned
`0700` directory. The reviewed JSON file must also be process-owned and `0600`.
The container runs as UID 0 with all capabilities dropped, a read-only root
filesystem and no public published ports. Its one writable bind is the private
configuration parent. A process-lifetime file lock excludes competing router
instances until all selected requests finish, even after the admin listener
closes during graceful shutdown. Upgraded connections also retain ownership
until their proxy handlers finish. A crash-stale socket is removed only when its ownership and mode
match and a connection is explicitly refused. Live listeners and foreign files
are preserved.

- `GET /status` identifies protocol 1 and kind `deedkit-release-router`.
- `GET /config/` returns configuration and its opaque ETag.
- `POST /config/` requires that ETag in `If-Match`; a mismatch returns `412`.
- `GET /reverse_proxy/upstreams` returns selected-request leases by address.
- `POST /generations/<id>/retire`, with `If-Match` and `{}`, atomically fences
  further issuer-file admission. It refuses the active generation. Existing
  leases remain visible until they complete. Fenced requests return `410`.

An issuer's targets and admission fence are irreversible. Configuration updates
retain every generation row, including fenced rows, so deletion and re-addition
cannot reopen an issuer after restart. Strict configuration limits are 1 MiB and
4096 generation rows; reaching either requires a separately reviewed enrollment
plan. Unknown keys, duplicate JSON keys, invalid targets and trailing JSON fail
closed. Public requests cannot access the private control endpoints.

Control changes affect memory only. The verified operator journals the decision
and atomically persists the reviewed JSON in the mounted parent. Restart reads
that file; the router never restores an automatic or guessed configuration.

```sh
cd services/release-router
go test -race -count=1 ./...
go vet ./...
```

Tests use isolated local HTTP servers and synthetic tokens. The root Docker
integration proof exercises the built image and actual private control socket.
See [the release protocol](../../docs/operations/seamless-release.md) and its
first-enrollment limitations. Work is tracked in [DK-40](../../BACKLOG.md).
