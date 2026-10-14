# Chromium sandbox seccomp profile

Derived from Moby profiles commit `6fe7deb1b9fb7c0397a4593480d7d22b9ee8caef`:
https://raw.githubusercontent.com/moby/profiles/6fe7deb1b9fb7c0397a4593480d7d22b9ee8caef/seccomp/default.json
Original SHA256: `6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6`. See MOBY-LICENSE (Apache-2.0).

The default-deny profile and capability-gated rules are retained. Four rules
are appended for the current amd64 host: clone with CLONE_NEWUSER or CLONE_NEWPID set, unshare
with exactly CLONE_NEWUSER, and chroot for Chromium's new user namespace.
`clone3` retains its ENOSYS fallback. The container drops all host capabilities,
uses UID10001, read-only root and no-new-privileges; Docker AppArmor remains on.
No host sysctl or global daemon policy is changed. This file only applies to the
WhatsApp containers selected by compose.whatsapp.yaml. Run the offline sandbox
smoke after an OS/browser/container-policy update; failure must stop deployment.
