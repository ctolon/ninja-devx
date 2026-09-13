# Security policy

## Reporting

Report vulnerabilities through [GitHub private vulnerability reporting](https://github.com/ctolon/ninja-devx/security/advisories/new).
Do not disclose exploit details in an issue, discussion or pull request before coordination.
Include the package and dependency versions, relevant controller/settings, expected policy,
actual response, impact and a minimal reproduction. Remove real credentials and customer data.

Reports are reviewed by the repository maintainers. This project does not offer a paid
incident-response service or a guaranteed response deadline. The reporter and maintainers
should agree on disclosure timing after impact and available mitigations are understood.
Credit is included with the reporter's consent. If GitHub reporting is unavailable, use
GitHub's private support/reporting facilities; do not post an exploit publicly to obtain
attention.

## Supported releases

0.0.1 is the first alpha release candidate. After publication, security fixes target the
latest released version. Older alpha versions have no promised backport window. A security
fix may require a behavior change; release notes must describe the changed contract and
migration steps. The [support policy](https://ctolon.github.io/ninja-devx/project/support/)
lists framework compatibility separately from upstream security support.

## Scope

Report flaws in shipped code, generated source, defaults, documentation or examples that
can cause unauthorized access or unsafe deployment. Relevant areas include permission
composition, tenant/owner/object scope, credential management, idempotency isolation,
webhook destinations and signatures, upload ownership, hidden-field disclosure, audit
redaction and generated-code injection.

Report dependency flaws to the upstream project as well. A dependency issue can still
require a ninja-devx constraint or documentation change; it is not dismissed solely because
the vulnerable code is external. An intentionally disabled protection is not itself a
package flaw, but misleading documentation or an unsafe default is in scope.

## Trust boundaries and deployment responsibilities

- Tenant resolution must verify membership. A tenant header does not authenticate a tenant.
- Object checks, queryset filtering and field visibility are separate controls. Audit,
  webhook and log payloads need their own data policy.
- Custom persistence hooks and auth backends are trusted application code. Use the selected
  write alias; schedule external effects after commit or through an outbox. A database
  rollback cannot undo an external request.
- Durable idempotency requires an independently committed claim. An uncertain in-flight
  claim is not automatically retried; investigate its side effects before manual recovery.
- Webhook delivery is at-least-once. Receivers must verify signatures and timestamp bounds
  and deduplicate event IDs. Keep destination restrictions enabled and restrict outbound
  access at the network layer as well.
- API key plaintext is returned on creation and should not be logged. Webhook signing
  secrets must remain recoverable; configure encryption and controlled key rotation.
- Upload metadata validation is not malware detection or proof of file contents. Configure
  checksum/version requirements when needed, use an isolated serving origin and a storage
  retention policy. Serve or process the verified version, not an unpinned mutable key.
- Readiness and logging configuration should not expose credentials, internal endpoints
  or exception details. Run Django's deployment checks for the actual application settings.

Examples use local-development secrets, permissive hosts or demonstration authentication.
They explain library behavior and are not production deployment templates. Their READMEs
identify what must be replaced.

## Supply-chain controls

CI checks the committed lockfile, audits runtime/extras dependencies and runs CodeQL in
GitHub. Actions and Docker bases are pinned to immutable revisions; Dependabot proposes
updates. A release reuses the validated distribution artifact and publishes through PyPI
Trusted Publishing, with artifact attestations. Repository environments, branch/tag rules,
private reporting and PyPI publisher registration require maintainer setup; see the
[release runbook](https://ctolon.github.io/ninja-devx/project/releasing/).

A successful scan is evidence for the scanned versions and advisory data at that time.
It is not a guarantee that the package has no vulnerabilities.
