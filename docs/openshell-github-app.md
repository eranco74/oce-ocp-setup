# GitHub App access through protected OpenShell Git

Use OCE's repository registry and request-phase middleware to give a dedicated
Codex Agent access to selected GitHub repositories. The worker holds the App
signing key and persistent custody key; OpenShell injects short-lived scoped
credentials. Keep Credential Gateway selected for the Agent's model source.

The team deployment exercised this on 2026-11-03 with Red Hat OCE source
`574a7cb9c9cf6370a58c621956d0d9f5fc64bfea` and OpenShell
`0.1.6-rhaiv.3`. Git and GitHub API reads succeeded for both selected repositories;
an unselected public repository returned HTTP 403, and a model turn completed.
Repository writes were configured but were not exercised. This is a deployment
recipe, not a new OCE credential implementation or production qualification.

## Decide whether this configuration fits

For that release, review these restrictions **before stopping any Agent**:

- Selecting `drivers.credential_proxy` affects the whole Installation. A ready
  Gateway-owned Harness model source qualifies beside repository Git. Agents
  without repository bindings are rejected on future deployment in this
  configuration; additional Gateway tool credential sources are also unsupported.
  Recreating an Agent with the same configuration does not fix this. Inventory
  every Agent; choose an isolated Installation or an OCE compatibility fix if
  those other configurations must remain deployable.
- Protected execution refuses **all saved per-Agent network additions**, including
  non-GitHub hosts. Existing additions must be explicitly removed; the per-Agent
  network-rule editor cannot add them back in this mode. The approved Codex
  artifact rules are a separate, narrow exception, not general network access.
- Normal `Configuration.secretBindings` for gateway channels/plugins remain
  available. They do not provide arbitrary proxy-injected credentials or direct
  Secret delivery into the Codex sandbox.
- `github-repository` access is finite. With `sessionDurationSeconds: 86400`, the
  admitted revision gets a 24-hour grant. Tokens can renew only within it. This
  profile does not support standing approval in the exercised release. A new
  **Agent deployment** creates a new grant and restarts that Agent's runtime;
  upgrading/restarting OCE or OpenShell does not renew the running revision's
  grant. Plan this operationally before relying on continuing access.

Deleting disposable Agents was an owner-approved choice in the team rollout,
not a migration requirement. OCE deletion removes identity and workspace data.
Do not delete other Agents, their PVCs or their credentials as part of this recipe
without separate authorization and a data-retention decision.

The existing [PAT recipe](github-cli.md) and [network-policy demo](policy-demo.md)
describe ordinary Gateway configurations; they cannot be layered onto protected
Git unchanged. Review the selected release's
[credential compatibility guide](https://github.com/red-hat-data-services/openclaw-enterprise/blob/574a7cb9c9cf6370a58c621956d0d9f5fc64bfea/docs/guides/deploy/credential-proxy-upgrade.md).

## 1. Freeze the release and prepare private inputs

Complete [Gateway mTLS](openshell-mtls.md) first. Follow the
[upgrade baseline procedure](upgrades.md) to fetch main explicitly, then freeze
the installed source, chart, CLI and immutable controller/runtime/OpenShell image
references. Fetching a newer revision is not an image upgrade. Preserve the
existing database, Agent identity, model credential, channel bindings and PVCs.
Schedule a maintenance window; the target Agent will stop and redeploy.

Use the installed release's
[enrollment guide](https://github.com/red-hat-data-services/openclaw-enterprise/blob/574a7cb9c9cf6370a58c621956d0d9f5fc64bfea/docs/guides/repository-credentials/openshell-enrollment.md),
[Installation fragment](https://github.com/red-hat-data-services/openclaw-enterprise/blob/574a7cb9c9cf6370a58c621956d0d9f5fc64bfea/deploy/examples/repository-credentials/openshell.fragment.yaml)
and [registry example](https://github.com/red-hat-data-services/openclaw-enterprise/blob/574a7cb9c9cf6370a58c621956d0d9f5fc64bfea/deploy/examples/repository-credentials/openshell.registry.json).
These pinned links document the exercised release; review differences if the
installed release changes. Use OCE's normal Installation-change and upgrade
helpers, rather than copying a lifecycle implementation into this repository.

Build candidates from **live** Installation and Helm values, outside both
checkouts. Never start from chart defaults. Merge the repository Backend and
Driver selections while retaining the existing Credential Gateway, mTLS,
runtime channels, resources and image digests. Retain the persistent custody key
across every restart and later upgrade.

Use operator-verified App, installation and numeric repository IDs in an immutable,
versioned registry ConfigMap. Authorize only the intended OCE Namespace and
`github-repository` profile. All repositories selected together must share the
same approved permission map. The team rollout selected metadata read plus
contents, pull-request and issue writes for two repositories. Do not substitute
an installation-wide token or assume this profile imposes branch restrictions;
GitHub permissions and branch rules still apply.

Prepare the worker-only signing-key and 32-byte custody-key Secrets through the
release's procedure. Keep all values, registry contents, key material, kubeconfigs
and verification logs private. Copy only the Gateway JWT **public** key into
`oce-openshell-gateway-public`; its `gateway_id` and `kid` must match the private
Installation verifier. The Gateway signing private key stays on the Gateway.

## 2. Use OpenShift service CA for middleware TLS

The middleware needs server TLS for one internal Service hostname, so consume
OpenShift's service serving certificate and injected public CA. This is separate
from the cert-manager administrator client identity used for Gateway mTLS.

Inspect the optional resources offline:

```bash
oc kustomize manifests/openshift/openshell-git
```

After authorization for the cutover, apply that Kustomization to the selected
cluster. It pre-creates the chart's Service so service-ca can issue
`oce-openshell-git-tls` **before** the worker needs the Secret. Helm adopts the
Service using its release annotations. It also creates a dedicated injected
`oce-git-service-ca` ConfigMap in `openshell-system`; do not store other data in
that ConfigMap. Review the fixed `oce` release name, namespaces and selectors
against live resources before applying.

Require both `tls.crt` and `tls.key` on the generated Secret, the exact serving SAN
`openclaw-enterprise-openshell-git.openclaw-system.svc`, and a nonempty
`service-ca.crt` on the injected ConfigMap before proceeding. Never print the key.

Merge [the OCE fragment](../manifests/openshift/openshell-git/oce.fragment.yaml)
into current private values, filling the registry name and approved upstream
CIDRs, and matching the separately created key Secret names. Merge
[the Gateway fragment](../manifests/openshift/openshell-git/gateway.fragment.yaml)
into current private Gateway values. Append volumes, mounts and middleware
entries: Helm replaces arrays, so passing this fragment alone can discard
existing entries. Retain mTLS and `scripts/openshell-mtls-post-render` on later
Gateway upgrades.

Use GitHub's current [Meta API](https://api.github.com/meta) `api` ranges that cover
the worker-observed issuer addresses. A single DNS answer can become stale
between requests; the team preflight encountered this. Keep the rule restricted
to verified provider ranges on TCP 443 and recheck the actual worker connection.

Apply [the supervisor egress allowance](../manifests/openshift/openshell-git/supervisor-egress.yaml)
with an explicit `oc -n` for each observed supervisor namespace, and append the
same object to `drivers.sandbox.configuration.gateway.networkPolicyResources`
in the private Installation for future workspaces. Match observed Gateway and
supervisor labels; do not trust workload Pods. If Gateway egress is already
isolated, extend its existing allowances for the worker on TCP 8445 without
dropping DNS, Kubernetes API or other required access. Do not introduce a new
egress-only policy granting just 8445 on an otherwise unrestricted Gateway.

OpenShift manages the serving Secret and CA projection. Check whether the selected
worker and Gateway reload those files; restart clients/listeners after rotation
when necessary. CA injection alone does not prove an existing process reloaded it.

## 3. Cut over in dependency order

1. Inventory Agent credential bindings and saved network additions. Resolve the
   Installation-wide compatibility decisions above. Obtain explicit approval to
   remove the target's additions; save their version and use a version-checked
   update with `additions: []`. Do not silently discard unrelated access.
2. Stop the target Agent through OCE and wait for `activeRevisionId` to clear.
   Stop preserves identity and PVCs; deletion does not. Keep its reusable
   Configuration, Harness auth and channel/plugin selections unchanged.
3. Remove the uninspected `source-control` GitHub policy from the private
   Installation only as part of this protected replacement. Declare the selected
   release's approved Codex artifact rules through `policy.codexArtifactRuleNames`.
   GitHub hosts and IP-address grants cannot also appear in another route.
4. Validate Installation startup, Helm/YAML rendering, worker-only key mounts,
   listener/issuer/audience/key-ID agreement and required network policies. Use
   `scripts/validate-installation` and the selected OCE release's procedures.
   Update the private Installation checksum and Secret reference together.
5. Roll out OCE first, retaining all selected images. Require a ready worker
   listening on 8445. Before admitting repository access, run the release's
   `docs/assets/openshell-quickstart/verify-github-installation.mjs` **inside that
   worker** with its mounted key. Require verified TLS, HTTP 200 and matching
   App/installation IDs. This GET-only check must request no installation token.
   Keep the Agent stopped if it fails; correct the observed prerequisite.
6. Upgrade the Gateway with the required `http_request_pre_credentials` binding
   and service-CA trust. Startup calls the worker's authenticated `Describe`, so
   reversing this order can prevent Gateway startup. Require readiness and live
   `oce-prepared-git` middleware discovery over authenticated management TLS.
7. Update the Agent with its current `configurationId` and the exact approved
   `repositoryBindings`. Preserve model auth and other settings. Deploy it once
   through OCE, then inspect durable deployment status until `succeeded`.

Do not replay an issuance or write whose outcome is uncertain. Preserve the
original operation's recovery bound and follow OCE's Token Service recovery and
retirement procedure. Blindly switching Helm values back does not settle
outstanding credentials; keep the signing/custody material and old owner available
until the release's cleanup evidence permits removal.

## 4. Verify and hand off

Through the actual Agent, run `git ls-remote --exit-code` against each selected
HTTPS `.git` URL and `gh api repos/OWNER/REPOSITORY --jq .full_name`. Confirm an
unselected public repository is denied, so public visibility cannot mask a
missing authorization boundary. Require a real model turn too. Keep verification
read-only unless a separate write test is authorized; do not print credentials,
run `gh auth token`, or send the result to Slack automatically.

Verify authenticated Gateway management succeeds and anonymous protected RPCs
return gRPC 16. Compare Agent identity, model binding, Configuration generation,
PVC UIDs and Gateway JWT key material to the private baseline. Confirm OCE and
OpenShell Helm releases are deployed and controllers are ready. Close temporary
management tunnels.

Record the exact active revision and `repositoryCredentials.deadlineWallMs` from
the deployment response, expressed in UTC and the operator's timezone. Record
the private current Helm/Installation input paths for the next upgrade. Tell the
owner when the finite grant expires and that renewing it requires a new Agent
deployment. Do not describe this release as unattended continuing repository access.
