# OpenShell mTLS cutover

Replace the existing NetworkPolicy-bounded plaintext management connection with
authenticated HTTPS. Use mTLS for OCE API/worker and retain Keycloak OIDC for the
dashboard. The user selected mTLS and requested OpenShift-managed certificate
lifecycle where appropriate.

Use the installed cert-manager and the pinned OpenShell chart's PKI resources.
OpenShift service-ca is appropriate for a Service's `.svc` serving identity, but
does not supply this deployment's client certificates or all OpenShell internal
DNS SANs. Bearer-token authentication is supported but adds token lifecycle work.
The implementation choice is cert-manager with a dedicated OCE client Certificate.

Supply opt-in Gateway/dashboard overlays, a CA-lifetime post-renderer, and an
operator runbook. Keep CA private keys confined to the PKI Secret and cert-manager;
mirror only the OCE leaf credential and public CA to the OCE namespace. Explicitly
document the operator-driven cross-namespace renewal and OCE restart requirement.

Preserve images, existing OIDC settings, Gateway JWT/encryption Secrets, database,
PVCs, model sources, Agent identities and configurations. Keep the default
plaintext test profile available until a separately authorized cutover. Do not
enable GitHub middleware, registry policy, or repository bindings in this PR.

Stage certificates and render candidates before the maintenance window. Freeze
operator changes, quiesce OCE writers, back up OpenShell consistently, change
Gateway transport, update dashboard/OCE clients, and verify authentication and
retained Agent workflows. Coordinate runtime repair/redeployment if required by
the transport change. Retain full rollback inputs; make no downtime guarantee.

Validation is offline YAML/Kustomize/Helm rendering, shell syntax, documentation
links and whitespace. Live TLS, authentication, renewal and workload proof belong
to the separately authorized cutover, not PR preparation.
