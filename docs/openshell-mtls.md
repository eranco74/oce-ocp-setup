# Cut over an existing OpenShell installation to mTLS

Use this procedure after the Red Hat OCE and OpenShell 0.1.6 image upgrades.
It changes management transport, not images or repository access. OCC's API and
worker use a dedicated mTLS client; the dashboard keeps its Keycloak user token.
Preserve the existing Credential Gateway, sources, Agent identities and PVCs.
Schedule a maintenance window: OCC, dashboard and Agent connections can pause.

The 2026-11-02 Red Hat runtime restored `git` and `curl`, so existing
`source-control` and curl policies are active again. This management-only change
leaves those policies in place. Remove the uninspected GitHub rule only in the
separate reviewed [repository-middleware cutover](openshell-github-app.md), when
its protected replacement is ready.

This procedure is for the existing `openshell-gateway` and `openshell-dashboard`
releases in `openshell-system`, and `oce` in `openclaw-system`. Review names and
live state before use. Obtain authorization for the cutover separately from
reviewing this documentation. Do not apply these resources merely to validate a PR.

## Certificate ownership

Use the cluster's existing cert-manager and the pinned OpenShell chart's Issuer
and Certificate resources. OpenShift's [service serving certificates](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/security_and_compliance/configuring-certificates)
cover a Service's `.svc` name, but do not supply the administrator client identity
or all OpenShell's internal SANs. Do not repurpose the cluster service CA as a
client-signing CA or use public ACME certificates for internal names.

The Gateway overlay enables cert-manager and certificate authentication explicitly
alongside OIDC. It disables anonymous management and leaves the broader mTLS
`operator` role disabled. The chart's normal client remains separate from the
dedicated `oce-openshell-admin` Certificate. Never mount OCE's private key in a
dashboard or sandbox. Dashboard trust contains only the public CA.
The OCE Certificate carries `OU=openshell-user` and `OU=openshell-admin`:
OpenShell maps certificate OUs to roles, and a trusted CN alone grants neither.
These must match the Gateway's user/admin role names. Do not add `OU=operator`.

The post-renderer gives the chart CA a ten-year lifetime, renewal one year before
expiry, and stable key on renewal. Leaf certificates last 90 days and renew
15 days before expiry. This does not promise unattended end-to-end renewal:
cert-manager updates the issuing namespace, while the operator must synchronize
OCE's copied leaf credential and restart its clients. Plan CA rotation separately.
Always retain the post-renderer on subsequent Gateway Helm upgrades.
It marks the chart PKI resources for retention so a transport rollback does not
garbage-collect their Certificates/Issuers and potentially their backing Secrets.
Fresh `openshell-mtls-server-tls` and `openshell-mtls-client-tls` Secret names
avoid adopting the old chart's long-lived generated certificates: cert-manager
can report an imported certificate Ready without reissuing it under the new CA.
Keep the old Secrets for rollback and verify issuer, chain and lifetime, not only
the Certificate Ready condition.
The serving SANs include `*.openshell.localhost` for OCE's routed Sandbox
WebSocket URLs. The chart's `*.openshell-gateway.localhost` default does not
cover those names; management RPC health alone will not detect that mismatch.

## 1. Capture the baseline and prepare candidates

Use the source checkout, CLI, Helm chart and workspace dependencies matching the
installed controller. Freeze their revisions and current image digests using the
[upgrade baseline procedure](upgrades.md). Do not resolve new images. Use Helm 3,
yq v4, and a dedicated kubeconfig. Keep every generated file outside both checkouts.

```bash
set -euo pipefail
umask 077
: "${OCE_CHECKOUT:?Set the checkout matching the installed OCE release}"
: "${KUBECONFIG:?Set the dedicated target-cluster kubeconfig}"
: "${CUTOVER_DIRECTORY:?Set a new absolute private directory outside both checkouts}"
: "${OPENSHELL_CHART:?Set the verified helm-chart-0.1.6.tgz path}"
: "${DASHBOARD_CHART:?Set the verified openshell-dashboard-0.1.0.tgz path}"
test ! -e "$CUTOVER_DIRECTORY"
install -d -m 700 "$CUTOVER_DIRECTORY"
export CUTOVER_DIRECTORY
oc whoami
oc whoami --show-server
oc config current-context

helm get values oce -n openclaw-system -o yaml > "$CUTOVER_DIRECTORY/oce-before.yaml"
helm get values openshell-gateway -n openshell-system -o yaml > "$CUTOVER_DIRECTORY/gateway-before.yaml"
helm get values openshell-dashboard -n openshell-system -o yaml > "$CUTOVER_DIRECTORY/dashboard-before.yaml"
oc -n openclaw-system get deployments -o json > "$CUTOVER_DIRECTORY/deployments-before.json"
oc -n openshell-system get statefulset openshell-gateway -o json > "$CUTOVER_DIRECTORY/gateway-before.json"
oc get pvc -A -o json > "$CUTOVER_DIRECTORY/pvcs-before.json"

installation_secret=$(yq -er '.installation.secretName' "$CUTOVER_DIRECTORY/oce-before.yaml")
installation_key=$(yq -r '.installation.key // "installation.yaml"' "$CUTOVER_DIRECTORY/oce-before.yaml")
export installation_key
oc -n openclaw-system get secret "$installation_secret" -o json |
  jq -er --arg key "$installation_key" '.data[$key]' | base64 -d \
  > "$CUTOVER_DIRECTORY/installation-before.yaml"
baseline_checksum=$(shasum -a 256 "$CUTOVER_DIRECTORY/installation-before.yaml" | cut -d ' ' -f 1)
test "$baseline_checksum" = "$(yq -er '.controlPlane.installationChecksum' "$CUTOVER_DIRECTORY/oce-before.yaml")"
cp "$CUTOVER_DIRECTORY/installation-before.yaml" "$CUTOVER_DIRECTORY/installation.yaml"
cp "$CUTOVER_DIRECTORY/oce-before.yaml" "$CUTOVER_DIRECTORY/values.yaml"
```

Back up the existing JWT and credential-encryption Secrets through your protected
backup process; do not print them. Record every running Agent/revision and source
ID, including team-claw, before the window. Verify Namespace visibility. Stop if
there is an upgrade in progress, an unexpected credential owner, an existing TLS
configuration, or unsupported chart differences. This is not a key-rotation recipe.

If the baseline checksum differs, reconcile the live Helm values and Installation
before proceeding; do not repair an image-upgrade helper's inputs mid-upgrade.
Preserve the live `credential_gateway.binaries` list, including Codex's
`0.164.0` pnpm path and any older path retained through the image transition.
Do not replace that list with the fresh-install fragment. For Agents with Slack
enabled, confirm the Installation already has `runtime.channels`; preserve it
and the managed proxy settings. See the [upgrade prerequisites](upgrades.md#2-prepare-the-upgrade)
and [Slack preparation](slack.md). Correct missing prerequisites separately before
this transport cutover.

Render the Gateway candidate with the **live** values first, retaining OIDC,
Red Hat images, scheduling, encryption/JWT Secret names and storage settings:

```bash
helm template openshell-gateway "$OPENSHELL_CHART" -n openshell-system \
  --is-upgrade --set agentSandbox.preflight.enabled=false \
  -f "$CUTOVER_DIRECTORY/gateway-before.yaml" \
  -f manifests/openshift/openshell-mtls/gateway-values.yaml \
  --post-renderer "$PWD/scripts/openshell-mtls-post-render" \
  > "$CUTOVER_DIRECTORY/gateway-rendered.yaml"

yq -i 'with(.backend[] | select(.type == "openshell");
  .configuration.endpoint = "https://openshell-gateway.openshell-system.svc:8080" |
  .configuration.rootCertificatePath = "/etc/openclaw/openshell/ca.crt" |
  .configuration.auth = {"mode":"mtls",
    "certificatePath":"/etc/openclaw/openshell/tls.crt",
    "privateKeyPath":"/etc/openclaw/openshell/tls.key"} |
  del(.configuration.insecureTransport))' "$CUTOVER_DIRECTORY/installation.yaml"
yq -i '.backend.openshell.clientSecretName = "oce-openshell-admin"' "$CUTOVER_DIRECTORY/values.yaml"
export OCC_INSTALLATION_CHECKSUM
OCC_INSTALLATION_CHECKSUM=$(shasum -a 256 "$CUTOVER_DIRECTORY/installation.yaml" | cut -d ' ' -f 1)
yq -i '.controlPlane.installationChecksum = strenv(OCC_INSTALLATION_CHECKSUM)' "$CUTOVER_DIRECTORY/values.yaml"
scripts/validate-installation "$CUTOVER_DIRECTORY/installation.yaml"
helm template oce "$OCE_CHECKOUT/deploy/helm/openclaw-enterprise" -n openclaw-system \
  -f "$CUTOVER_DIRECTORY/values.yaml" > "$CUTOVER_DIRECTORY/oce-rendered.yaml"
helm template openshell-dashboard "$DASHBOARD_CHART" -n openshell-system \
  -f "$CUTOVER_DIRECTORY/dashboard-before.yaml" \
  -f manifests/openshift/openshell-mtls/dashboard-values.yaml \
  > "$CUTOVER_DIRECTORY/dashboard-rendered.yaml"
```

Inspect private diffs. Require exactly one OpenShell Backend. Check that the CA
private key is not projected into workloads; OCE's leaf mounts only in API/worker;
the dashboard receives only public CA trust and retains OIDC. Check unchanged
images, ServiceAccounts, PVC claim template, Gateway identity/JWT/encryption keys,
and NetworkPolicies. Keep TCP 8080 private; TLS does not replace those policies.
The preflight override above is offline-only; retain the real upgrade preflight.

## 2. Stage PKI without changing serving transport

After review and task authorization, stage only the chart's cert-manager
resources. The ownership metadata lets the subsequent Helm upgrade adopt the
staged objects. The CA Secret stays in `openshell-system`.

```bash
yq 'select(.apiVersion == "cert-manager.io/v1") |
  .metadata.annotations."meta.helm.sh/release-name" = "openshell-gateway" |
  .metadata.annotations."meta.helm.sh/release-namespace" = "openshell-system"' \
  "$CUTOVER_DIRECTORY/gateway-rendered.yaml" > "$CUTOVER_DIRECTORY/pki.yaml"
oc apply -f "$CUTOVER_DIRECTORY/pki.yaml"
oc -n openshell-system wait --for=condition=Ready certificate/openshell-gateway-ca --timeout=3m
oc -n openshell-system wait --for=condition=Ready certificate/openshell-gateway-server --timeout=3m
oc -n openshell-system wait --for=condition=Ready certificate/openshell-gateway-client --timeout=3m
oc apply -k manifests/openshift/openshell-mtls
oc -n openshell-system wait --for=condition=Ready certificate/oce-openshell-admin --timeout=3m

# Copy only the dedicated leaf credential and public CA, never the CA private key.
oc -n openshell-system get secret oce-openshell-admin -o json |
  jq '{apiVersion:"v1",kind:"Secret",type:"kubernetes.io/tls",
    metadata:{name:"oce-openshell-admin",namespace:"openclaw-system"},
    data:{"ca.crt":.data["ca.crt"],"tls.crt":.data["tls.crt"],"tls.key":.data["tls.key"]}}' |
  oc apply --server-side --field-manager=oce-mtls-secret-sync -f -
oc -n openshell-system get secret oce-openshell-admin -o json |
  jq '{apiVersion:"v1",kind:"Secret",type:"Opaque",
    metadata:{name:"openshell-dashboard-gateway-ca",namespace:"openshell-system"},
    data:{"ca.crt":.data["ca.crt"]}}' |
  oc apply --server-side --field-manager=oce-mtls-secret-sync -f -
```

Validate certificate SANs, issuer, usages, expiry and key pairing privately.
Server-side apply avoids storing private key material in a last-applied annotation;
stop on an unexpected ownership conflict rather than forcing it.
Restrict CA issuance and Secret access: certificates signed by the trusted client
CA are privileged. Leaf renewal alone does not revoke old certificates.

## 3. Perform the cutover

Freeze other operator changes. Quiesce OCC and dashboard clients, then stop the
Gateway before taking a consistent backup of its SQLite PVC:

```bash
oc -n openclaw-system scale deployment/openclaw-enterprise-api deployment/openclaw-enterprise-worker --replicas=0
oc -n openshell-system scale deployment/openshell-dashboard --replicas=0
oc -n openshell-system scale statefulset/openshell-gateway --replicas=0
```

Wait for all those Pods to terminate. Take a cold CSI VolumeSnapshot of the
observed `openshell-data-openshell-gateway-0` claim using an approved
VolumeSnapshotClass compatible with its CSI driver. This cluster currently has
`csi-aws-vsc` for `ebs.csi.aws.com`, with **Delete** retention: retain the snapshot
object and confirm `status.readyToUse: true`, its bound content, and the recovery
procedure before continuing. If snapshot support or recovery is unavailable,
stop and arrange a supported cold-volume backup. Do not copy a live SQLite file.

```bash
helm upgrade openshell-gateway "$OPENSHELL_CHART" -n openshell-system \
  -f "$CUTOVER_DIRECTORY/gateway-before.yaml" \
  -f manifests/openshift/openshell-mtls/gateway-values.yaml \
  --post-renderer "$PWD/scripts/openshell-mtls-post-render" --wait --timeout 7m
oc -n openshell-system rollout status statefulset/openshell-gateway --timeout=5m

# Patch only the Installation document, retaining Secret ownership metadata.
jq -n --arg key "$installation_key" --rawfile doc "$CUTOVER_DIRECTORY/installation.yaml" \
  '{data:{($key):($doc|@base64)}}' |
  oc -n openclaw-system patch secret "$installation_secret" --type merge --patch-file /dev/stdin
helm upgrade oce "$OCE_CHECKOUT/deploy/helm/openclaw-enterprise" -n openclaw-system \
  -f "$CUTOVER_DIRECTORY/values.yaml" --wait --timeout 7m
helm upgrade openshell-dashboard "$DASHBOARD_CHART" -n openshell-system \
  -f "$CUTOVER_DIRECTORY/dashboard-before.yaml" \
  -f manifests/openshift/openshell-mtls/dashboard-values.yaml --wait --timeout 7m
```

Use OCE's [Installation-change procedure](https://github.com/openclaw/openclaw-enterprise/blob/574a7cb9c9cf6370a58c621956d0d9f5fc64bfea/docs/guides/deploy/production-installation-changes.md)
for startup failures and recovery. Do not reinitialize OCC, migrate ownership,
recreate model sources, or redeploy the fleet as an implicit part of a Helm update.

## 4. Verify before ending the window

- Require API, worker, Gateway and dashboard readiness. Verify OCE's authenticated
  Workspace/Sandbox/provider operations using the existing sources and revisions;
  a health endpoint or TLS handshake alone is insufficient.
- In a real browser, sign in to the dashboard through Keycloak and perform a
  read permitted to that user. Verify a non-admin remains non-admin. Do not give
  the dashboard an administrator client certificate to work around an OIDC error.
- From an allowed caller location, use the matching OpenShell CLI or `grpcurl`
  to call a protected Workspace-list RPC without a client certificate or bearer
  token; require authentication failure. Reflection and Health can be public and
  do not establish this denial. Verify the same operation succeeds for OCE's
  authorized mTLS client. Never disable certificate verification for a probe.
- Verify every previously running Agent's model turn and its required tool
  credentials, including team-claw. Compare PVC UIDs and source/Agent identities
  to the baseline. Existing HTTP supervisors keep a fixed management endpoint;
  plan new OCE runtime revisions to adopt TLS. OpenShell stop/start may refuse
  the old Sandbox while it is stuck in `Provisioning`. Use OCE's normal Agent
  deployment flow, test one canary before the remaining running Agents, and
  preserve stopped Agents' state. First compare each saved Configuration's ID
  and generation and the Agent's selections with the active revision: deploying
  a newer draft also activates its pending edits and requires owner coordination.
  Keep state migrations with the selected release's helpers. If Doctor reports
  uncertain command cleanup, preserve its artifacts and investigate that owner;
  do not force a schema change or repeatedly replay migration.
- Record image digests, chart/source revisions, certificate fingerprints,
  authentication outcomes and any unverified workflow in private evidence.

This establishes the management transport prerequisite only. GitHub registry,
middleware, worker authority and Agent repository bindings remain separate work.

## Renewal and CA rotation

Monitor cert-manager Certificate readiness and `status.renewalTime`/`notAfter`.
When `oce-openshell-admin` renews, repeat the leaf-copy command in step 2, verify
the destination certificate matches, and roll OCC's API and worker through the
Installation-change procedure. OCE reads client key material when constructing
its connection; do not assume a projected Secret update reloads an existing client.
Do this inside the 15-day renewal window, before the old leaf expires. Keep the
operator synchronization in the deployment's renewal schedule; this PR installs
no Secret-replication controller or automated restart controller.

The Gateway watches TLS files for new handshakes. Verify renewed serving/peer
certificates are actually used. The dashboard trusts the CA, not a leaf pin.
For root renewal or key rotation, coordinate overlap, both namespaces' trust,
all client credentials, and connection restarts; never delete the CA Secret to
force renewal. Keep the long-lived CA post-renderer on every later Helm upgrade.

## Rollback

On failure, stop new work and preserve evidence. Quiesce OCC/dashboard as in step 3. Restore the original Installation document with the same Secret-patch command,
using `installation-before.yaml`, then upgrade the Gateway with
`gateway-before.yaml`, OCE with `oce-before.yaml`, and the dashboard with
`dashboard-before.yaml`, using their original charts and images. These original
values restore plaintext management; recheck the private NetworkPolicy boundary.
Use the original recorded replica counts if any release had been scaled down
before this procedure. Require the same source/Agent/model/dashboard checks.
Transport rollback does not rewind Agent versions: supervisors created during
the cutover now retain HTTPS endpoints. Recover affected Agents through OCE again
after restoring the original transport, with the same draft/generation checks.

Do not delete JWT/encryption Secrets, CA or leaf Secrets, PVCs, or snapshots during
rollback. Existing SQLite data normally needs no restore for a transport-only
rollback. If data recovery is needed, use the approved cold-snapshot recovery
procedure rather than overwriting a mounted database. Retain newly staged PKI
until recovery and credential retirement are explicitly reviewed.
