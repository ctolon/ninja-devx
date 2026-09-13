# Contributing

Changes should solve a concrete application or maintenance problem. For a substantial API
change, describe the use case, proposed interface, alternatives and compatibility cost in
an issue before building it. Security reports belong in [SECURITY.md](https://github.com/ctolon/ninja-devx/blob/main/SECURITY.md); project
participation follows the [Code of Conduct](https://github.com/ctolon/ninja-devx/blob/main/CODE_OF_CONDUCT.md).

## Development

Use Python 3.11 or later and [uv](https://docs.astral.sh/uv/). The default validation
interpreter is Python 3.13; supported combinations are listed in the support documentation.

```bash
uv sync --locked --group docs --group verification
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
uv run --no-sync pyright --pythonpath .venv/bin/python
uv run --no-sync python tools/messages.py --check
uv run --no-sync python tools/generate_docs.py --check
uv run --no-sync mkdocs build --strict
uv run --no-sync python tools/check_docs_links.py site
```

Run backend-dependent changes in the disposable Docker stack described in the
[release runbook](https://ctolon.github.io/ninja-devx/project/releasing/). Local SQLite tests
do not establish PostgreSQL lock behavior, Redis counter atomicity or S3 upload semantics.

Each example is a separate Django project. Run it from its directory, for example:

```bash
cd examples/blog
uv run --no-sync --project ../.. pytest -q
uv run --no-sync --project ../.. mypy blog config clients/blog_client.py
uv run --no-sync --project ../.. python manage.py makemigrations --check --dry-run
```

## Design and review

- Keep HTTP adaptation separate from a business rule that has non-HTTP callers. A service
  or repository interface is useful when it owns a real boundary, not merely another name
  for every ORM call.
- Preserve request, object and queryset authorization boundaries. Custom hooks must state
  their transaction and database ownership. Test rejection paths and side effects.
- Keep request state out of shared controller/permission objects. Resource dependencies
  need success, failure and cancellation cleanup tests.
- Public APIs must pass strict mypy/Pyright checks. Avoid widening a public type to hide a
  mismatch. Implementation modules are not automatically public extension points.
- Add a regression test for a bug. Prefer tests of observable behavior over copies of the
  implementation. Concurrency changes need deterministic coordination and deadlines.
- Update guides/examples, changelog and migration notes when behavior changes. Explain the
  trigger, resulting behavior, limitations and operational requirements in plain language.
  Avoid unsupported performance claims or feature rankings.
- Wrap user-facing messages in Django gettext and refresh the matching catalogs with
  `tools/messages.py`. Regenerate option references with `tools/generate_docs.py`.

## Dependencies and generated files

Edit dependency constraints intentionally and regenerate `uv.lock`; do not submit a stale
lock. CI uses `--locked`. Docker base digests and action commit pins require reviewed updates;
Dependabot proposals are input to that review, not automatic authorization to merge.

Do not edit generated option tables, sync twins or API clients manually. Run their documented
generator and review the diff. A generated artifact can still expose an incorrect public
contract; successful generation is not sufficient review.

## Pull requests

Describe the concrete problem and final behavior for a reviewer who has not followed the
issue. Include relevant validation and material limits. Keep unrelated refactors separate.
Contributions are licensed under Apache-2.0; do not include material you cannot license
under the repository's terms.

Maintainers use the [release runbook](https://ctolon.github.io/ninja-devx/project/releasing/)
for tag validation, test gates, Trusted Publishing, recovery and repository settings.
Do not publish a tag or distribution as part of an ordinary contribution.
