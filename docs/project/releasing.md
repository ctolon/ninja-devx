# Release runbook

The package version in `pyproject.toml` is the release version; every release before 1.0 is
an alpha. The files define validation and publication workflows; they do not configure GitHub account settings or publish a package
by themselves. The maintainer owns the decision to create a release tag.

## One-time repository and PyPI setup

1. Confirm the GitHub repository and PyPI project names. Register a
   [PyPI Trusted Publisher](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
   for owner `ctolon`, repository `ninja-devx`, workflow `release.yml`, environment `pypi`.
   Use PyPI's pending-publisher setup if the project does not yet exist.
2. Create the GitHub `pypi` environment. Require a maintainer review and restrict deployment
   to protected version tags. The job receives an OIDC token only at publication time;
   no long-lived PyPI password/token is stored in the workflow.
3. Protect `main` against direct/force pushes and require successful CI jobs and review.
   Require CODEOWNERS review for workflow, dependency and security-policy changes. Protect
   `v*` tags from update/deletion and restrict their creation to release maintainers.
4. Enable private vulnerability reporting, Dependabot alerts and code scanning. Confirm
   the private reporting link and the Code of Conduct contact are monitored. Check that
   GitHub's repository plan supports the configured scanning/attestation features.
5. Set GitHub Pages to **GitHub Actions**. The `github-pages` environment publishes the
   `main` branch documentation. Configure a custom domain separately if one is required.

These are account/repository settings, not properties a YAML file can enforce on its own.
Review the [GitHub environment documentation](https://docs.github.com/en/actions/deployment/targeting-different-environments/managing-environments-for-deployment)
and [artifact attestation documentation](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations).

## Local Docker validation

Docker Engine and Compose are the only host runtime requirements. Run from the repository
root:

```bash
docker compose -p ninja-devx-check build
docker compose -p ninja-devx-check up -d --wait postgres redis s3
docker compose -p ninja-devx-check run --name ninja-devx-check-tests --no-deps tests
docker cp ninja-devx-check-tests:/tmp/devx-validation /tmp/ninja-devx-validation
docker compose -p ninja-devx-check down --volumes --remove-orphans
```

Use a distinct Compose project name if another validation is running. The stack exposes
no host ports and does not mount application databases or the Docker socket. PostgreSQL
and S3 data use temporary filesystems; credentials are test-only. Teardown removes only
this Compose project's resources. Do not use a global Docker prune command.

The test image pins Python 3.13, Node 22 and uv by image digest. It installs the committed
lockfile and TypeScript 5.9.3. The S3 service builds a pinned MinIO release through the Go
module proxy with checksum verification because the upstream binary image may be unavailable.
Both builds require registry/package-network access. Base-image updates are reviewed
changes; a digest gives reproducibility, not perpetual security support.

The default command runs the full test suite against PostgreSQL, Redis and S3, with branch
coverage, a 90% coverage floor (`tools/verify_local.py --fail-under 90`) and a 300-second
process-group deadline. Results are written in the test container.
Run SQLite separately by clearing the database environment:

```bash
docker compose -p ninja-devx-check run --rm --no-deps -e TEST_DATABASE_URL= tests \
  python -m pytest -q
```

For the remaining validation commands inside the same image:

```bash
docker compose -p ninja-devx-check run --rm --no-deps tests ruff check .
docker compose -p ninja-devx-check run --rm --no-deps tests mypy
docker compose -p ninja-devx-check run --rm --no-deps tests pyright --pythonpath .venv/bin/python
docker compose -p ninja-devx-check run --rm --no-deps tests mkdocs build --strict
```

Keep services running while commands that require them execute. `tools/verify_sdist.py`
uses an isolated SQLite test configuration for source-distribution checks.

## CI gates

| Job | Contract |
|---|---|
| Quality | Locked dependencies, Ruff, strict type checks, catalogs, generated docs, sync twin, strict docs and internal links |
| Compatibility | Supported Python/Django combinations with the committed Ninja resolution |
| Docker | Full suite with actual PostgreSQL, Redis and S3; coverage artifact and bounded teardown |
| Examples | Tests, type checks, model migrations, Django system checks and scaffold drift |
| Package | Wheel/sdist metadata, each extra in isolation, all extras together, sdist rebuild/docs/tests |
| Dependency audit | Runtime and optional dependency lock resolution checked against current advisories |
| Security workflow | CodeQL and scheduled dependency scan; findings require maintainer triage |

A failed security finding must be investigated, not hidden with an unbounded ignore list.
The CI matrix is a workflow definition until it has run on GitHub; local Docker evidence
covers the interpreter/framework/backend versions actually exercised.

## Prepare the candidate

- Keep `pyproject.toml`, the changelog heading and intended tag in agreement. Do not
  increment the version just to retry a build.
- Read release notes as a user: identify public behavior, migrations, optional dependencies
  and limits. Remove internal review chronology from published release notes.
- Verify install and migration instructions from a clean environment. Check examples,
  migration guides, declared support and generated clients after the final code changes.
- Run the full gates on the candidate commit. Retain coverage, backend, package and audit
  evidence. Test an upgrade with representative application data before promising it.
- Run `python tools/check_release.py refs/tags/v0.0.3` locally. It rejects branch refs,
  mismatched versions, missing/duplicate notes and unresolved note placeholders.

## Publication sequence

An authorized maintainer creates and pushes the immutable `v0.0.3` tag after reviewing the
candidate. The release workflow validates the tag, calls the complete CI workflow and
uses the **same distribution artifact** produced by its package job. It does not rebuild
between validation and publication.

The `pypi` environment review is the final publishing boundary. The publisher uses OIDC,
produces PyPI attestations and refuses to silently skip an already published version.
A separate job creates a GitHub prerelease from the checked changelog and attaches the
distributions. `--prerelease` reflects this initial alpha policy; update that policy
explicitly before a stable release series.

Do not move a published tag or replace a wheel with different bytes under the same version.
If the publish job fails, inspect PyPI before retrying. If publication succeeded but GitHub
notes failed, repair only the notes job. A defective published distribution requires a
new version and, where appropriate, a yank and advisory; deletion is not a rollback strategy.

## Documentation and post-release checks

Pages follows `main`; it is not a versioned archive. Tagged source distributions contain
the corresponding docs and examples. The navigation therefore has no misleading version
selector. Add versioned hosting only when its build/storage policy is implemented.

After publication, install the exact version from PyPI in a clean environment, confirm
metadata/extras/migrations and follow the Quickstart. Check the release assets, advisory
links and Pages. Record any intentional support limitation in the changelog and support
page before announcing availability. Release preparation does not send announcements or
publish tags automatically from a local workstation.

The MinIO test server is a separately licensed upstream dependency. Its source and license
are available from [MinIO](https://github.com/minio/minio); the Apache-2.0 license for
ninja-devx does not relicense that server. The repository supplies a test build recipe,
not a hosted storage service or a prebuilt server distribution.
