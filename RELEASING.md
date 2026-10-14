# Releasing AnyMsg

The public repository is a fresh source export, not the Personal OS git history.
Never publish correspondence, operational state, backups, session data or tokens.
Python and npm distribution versions must match `messaging_service.__version__`.
The current version is 0.9.9 (experimental); sending remains unimplemented.

1. Run CI with synthetic PostgreSQL, Python and Node tests. Build the wheel,
   sdist and npm tarball. Inspect their inventories; install them outside the
   checkout and verify CLI/help/imports and immutable SQL/Node assets.
2. Review staged paths and diff, plus private-data scans. Commit and tag the
   reviewed source. Keep deployment upgrades separate from registry releases.
3. Configure PyPI Trusted Publishing, then dispatch `publish-pypi.yml` on the tag.
4. For the first npm release, an authorized maintainer publishes the inspected
   tarball through `npm publish --access public`, completing the registry's
   owner authentication/2FA flow. Do not put OTPs or tokens in an issue/chat.
5. After that initial npm package exists, configure its Trusted Publisher for
   `GoSync-Inc/anymsg`, workflow `publish-npm.yml`, environment `npm`. Select
   direct publishing permission if using this workflow (not staged-only).
   A new npm publisher must successfully publish within two days or its
   configuration expires. Validate it with the next reviewed patch release;
   an already published version cannot be overwritten.
6. Verify the public registry versions and hashes, then link them from the
   GitHub prerelease. Never report publishing success based only on a build.

## PyPI first publication without a stored token

At https://pypi.org/manage/account/publishing/ add a **pending publisher**:

- PyPI project: `anymsg`
- GitHub owner: `GoSync-Inc`
- Repository: `anymsg`
- Workflow filename: `publish-pypi.yml`
- Environment: `pypi`

The GitHub environment must have exactly that name. The first successful OIDC
publication creates the project for the configuring PyPI account. The account
must satisfy PyPI's verification/2FA requirements. Existing projects configure
publishers in their project settings instead. No PyPI token is committed or
stored as a GitHub secret.

Both release workflows take a `version` input. PyPI requires the selected
matching git tag. npm may run from that tag or from `main`; in either case its
build-and-test job explicitly checks out `refs/tags/v<version>`, never untagged
source. This lets the publication workflow be repaired without moving an already
published release tag.
They fail if a package/version differs or if the version is already registered.
A shared reusable build-and-test workflow validates the source before upload.
Actions have minimum permissions; the upload job alone receives `id-token: write`.
Do not run publication on a pull request from an untrusted contributor.

Official guides: [PyPI pending publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
and [npm trusted publishers](https://docs.npmjs.com/trusted-publishers/).
