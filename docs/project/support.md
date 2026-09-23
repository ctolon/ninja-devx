# Support and stability

## Supported versions

<!-- support-matrix:start -->
| Component | Declared compatibility |
|---|---|
| Python | 3.11, 3.12, 3.13, 3.14 |
| Django | 4.2, 5.2, 6.0, 6.1 |
| django-ninja | `django-ninja>=1.7,<2` |
<!-- support-matrix:end -->

This table is generated from `pyproject.toml`. It declares intended compatibility;
individual verification runs record their installed versions and checks. See the
[changelog](changelog.md) for released changes and the [release runbook](releasing.md)
for the validation gates. A table entry alone is not proof that every Python/Django
combination was exercised in a given run.

## Versioning

**0.0.2** is the current alpha release and APIs may change between minor releases before
1.0. The following policy applies to published releases.

ninja-devx follows [semantic versioning](https://semver.org). Before 1.0, a minor release
(`0.x` → `0.x+1`) may contain breaking changes, and the changelog lists them with
migration notes. Patch releases aim to preserve the public API. Necessary security changes
are documented. The definitions below apply from 0.0.1; the deprecation policy becomes
binding at 1.0:

- **Public API** means every name exported from `ninja_devx`, `ninja_devx.crud`,
  `ninja_devx.layers`, `ninja_devx.testing.clients`, `ninja_devx.contrib.*` and
  `ninja_devx.codegen`. It also covers settings keys, management command options, system
  check ids and the pytest plugin's fixtures and options.
- Modules and names starting with `_` are private.
- Type-level changes that make correct code fail mypy or pyright strict count as
  breaking.
- Generated OpenAPI may gain documented responses in minor releases. Operation ids and
  paths do not change.

## Deprecation policy

Before 1.0, deprecations follow the same steps whenever a replacement exists.

1. A feature is deprecated in a minor release. Using it emits `DeprecationWarning`
   naming the replacement, and the changelog lists it under "Deprecated".
2. It is removed in the next minor release at the earliest, and never within a patch
   release.
3. Removals are listed under "Removed" with a migration note.

Security fixes may skip the policy when there is no other way; the changelog says so
explicitly.

## Verification

CI defines a Python/Django compatibility matrix, Docker-backed tests, example checks,
package installation checks and a runtime dependency audit. Local validation evidence
covers only the versions actually exercised. Upstream end-of-life policy is independent
of package import compatibility; production users must select supported upstream releases.

Benchmarks are developer tools, not a universal latency promise. See
[Performance](performance.md) and the [release runbook](releasing.md) for measured workloads
and release gates.

## API stability areas

The intended public surfaces are:

| Area | Surface and change policy |
|---|---|
| Core | Root symbols, CRUD and layers; published changes receive migration notes. |
| Optional integrations | Contrib backends, uploads, audit and tasks; require their extras and database migrations where documented. |
| Generated clients | Regenerate after API/schema changes; supported OpenAPI subset is documented and unsupported features fail generation. |
| Internals | `_internal`, routing compiler/invocation and DI execution modules; import through the documented facade where available. |

The root facade stays lazy so importing `ninja_devx` does not configure Django. ContextVar
keys and immutable definitions may remain module-level; their values belong to the
active execution context. Runtime registries and mutable schema caches have explicit
application/router/class owners.
