# Build and upgrade OCE

Run from this repository root with a dedicated, clean OCE checkout and protected
baseline files matching the live installation. Select upstream main or a reviewed
integration commit, then freeze that commit and immutable image digests. Building
never changes serving images. Review changes before applying them. Use a dedicated kubeconfig copy and keep
it unchanged through completion or recovery; the helper binds recovery to its
exact bytes.

Controller-only upgrades stop OCC's API and worker for migrations and rollout,
but do not request Agent deployments. Console and OCC-proxied operations pause;
other affected runtime dependencies can interrupt Agents. Runtime upgrades also
redeploy every running Agent concurrently and can interrupt connections and turns.
Preserve database and Agent state; this is not a zero-downtime workflow.

Run the upgrade and resume commands with Bash 4.4 or later. On macOS, the
system Bash 3.2 can fail during resume when every Agent already has a recorded
deployment response. Select a current Bash in `PATH` before starting; keep the
same source and evidence directory when resuming.

## 1. Build the selected candidate

To deploy published images instead of building, set the three variables below and follow [the published images](#use-the-published-images-instead-of-building) at the end of this section.

```bash
# REQUIRED: dedicated OCE release checkout, not another person's working tree.
export OCE_CHECKOUT='/absolute/path/to/openclaw-enterprise'

# REQUIRED: saved target-cluster connection and private installation files.
export KUBECONFIG='/absolute/path/to/kubeconfig'

export OCC_INPUT_DIRECTORY='/absolute/path/to/cluster-inputs'
```

**Choose one source.** For upstream main:

```bash
git -C "$OCE_CHECKOUT" fetch origin main

export OCC_REV="$(git -C "$OCE_CHECKOUT" rev-parse origin/main)"
```

For a reviewed checkout that includes unmerged PRs, use this **instead**:

```bash
test -z "$(git -C "$OCE_CHECKOUT" status --porcelain)"

export OCC_REV="$(git -C "$OCE_CHECKOUT" rev-parse HEAD)"
```

The wrapper resolves runtime source metadata from the selected revision's
`deploy/runtime/openclaw-source.json`, or its legacy Dockerfile when the manifest
is absent. It records the OpenClaw commit, archive checksum, and build timestamp
in the private `runtime-source.json` and passes all three to the runtime build.

The wrapper honors `OCC_REV`. Setting only `OCE_CHECKOUT` still selects upstream
main by default, which would omit unmerged PR changes.

```bash
# Choose controller for frequent OCC updates; runtime or both build those images.
scripts/build-images controller

oc -n openclaw-system get builds
```

Wait for the selected build to be Complete, then verify its architecture and
immutable digest. Use the same selection in both commands:

```bash
scripts/verify-images controller

source "$OCC_INPUT_DIRECTORY/images.env"

export OCC_REV="$(jq -er .oceCommit "$OCC_INPUT_DIRECTORY/runtime-source.json")"

# Move only the dedicated, clean release checkout to the exact built revision.
git -C "$OCE_CHECKOUT" switch --detach "$OCC_REV"

scripts/build-occ
```

The checkout must have its matching OCE workspace dependencies for startup
validation. Follow that checkout's dependency setup; do not reuse an incompatible
installed graph. OpenShell's approved Codex executable path must match the chosen
runtime image; review `manifests/openshift/openshell-installation-fragment.yaml`
when upstream changes its bundled Codex version.

### Use the published images instead of building

Skip the source selection and build above and resolve the images published at `quay.io/aipcc/base-images/agentic` instead. The [install guide](install.md#use-the-published-images-instead-of-building) describes the helper and its checks. Resolving never changes serving images.

The published images are built from the carry repository
`red-hat-data-services/openclaw-enterprise`, not upstream
`openclaw/openclaw-enterprise`. Set `OCE_CHECKOUT` to a checkout of the carry
repository: the resolver requires the image's OCE commit to be on that
checkout's `origin/main`, and the release checkout must freeze the chart and
CLI at the carry revision the image was built from. The release-order check
below then compares installed and published revisions within the carry
repository's main.

```bash
# REQUIRED: full SHA of the OCE revision behind the installed controller, from the last release record.
# Set it first: scripts/resolve-images overwrites published-images.json.
export INSTALLED_REV='<full commit SHA>'

git -C "$OCE_CHECKOUT" fetch origin main

scripts/resolve-images controller

source "$OCC_INPUT_DIRECTORY/images.env"

export OCC_REV="$(jq -er .oceCommit "$OCC_INPUT_DIRECTORY/published-images.json")"

if git -C "$OCE_CHECKOUT" merge-base --is-ancestor "$INSTALLED_REV" "$OCC_REV"; then
  # Move only the dedicated, clean release checkout to the exact published revision.
  git -C "$OCE_CHECKOUT" switch --detach "$OCC_REV"

  scripts/build-occ
else
  echo 'The published revision is older than the installed one; stop.' >&2
  false
fi
```

Check the release order yourself: `latest` can trail `origin/main`, and OCE's upgrade helper does not compare revisions. Take `INSTALLED_REV` from the release record saved with the last release: the `source-revision` file in its upgrade evidence directory, or `oceCommit` in the `runtime-source.json` or `published-images.json` kept with it. A later resolve overwrites `published-images.json`, and a build overwrites `runtime-source.json`.

When no release record exists on the machine, or the installed revision is no
longer fetchable (for example an in-cluster build from a rewritten carry
branch), take `INSTALLED_REV` from the cluster: the controller ImageStream tag
matching the deployed digest, or the deployed image's
`io.openshell.harness.version` label. The git ancestry check cannot run for an
unfetchable revision; before continuing, confirm the direction with the
evidence you have, such as the deployed and candidate runtime OpenClaw pins,
and that the candidate controller digest differs from the deployed one.

Pass `runtime` for the runtime image only, or no argument for both. A runtime-only run takes the OCE revision from `OCC_REV` and refuses a runtime image whose OpenClaw commit is not that revision's pin; build a deliberately newer runtime instead. Set `OCC_REV` to the checkout you upgrade with, not to a value left from the build path:

```bash
export OCC_REV="$(git -C "$OCE_CHECKOUT" rev-parse HEAD)"

scripts/resolve-images runtime

source "$OCC_INPUT_DIRECTORY/images.env"
```

The published images are built from the carry main, so a checkout with unmerged carry changes still needs the build above. The published runtime also has a different base image than the in-cluster build, for example `/usr/bin/python3.14` where the in-cluster build ships `/usr/bin/python3.11`, and it drops `/usr/bin/git` and `/usr/bin/curl` entirely. Compare executable paths in Installation allow-lists, such as sandbox policy ceilings, `binaries` and `toolBinaries`, before redeploying Agents: export the candidate image filesystem without running it and check every named path.

```bash
podman create --name runtime-inspect "$RUNTIME_IMAGE" >/dev/null
podman export runtime-inspect > runtime-root.tar
podman rm runtime-inspect >/dev/null
tar -tf runtime-root.tar | grep -x 'usr/bin/python3\(\.11\|\.14\)\?'
```

Paths absent from the image make their Installation entries inert rather than
failing the upgrade: on 2026-10-22 the `source-control` sandbox network policy
(github.com for `/usr/bin/git`) and the demo and docs sandbox ceilings naming
`/usr/bin/python3.11` and `/usr/bin/curl` went inert on the published runtime,
while the Codex executable, `/usr/local/bin/node` and `/usr/local/bin/gh` were
unchanged, so Agent execution and model turns were unaffected. Removing the
scratch image export afterwards keeps the private input directory small.

### Switch an in-cluster installation to the published images

An installation built through the in-cluster BuildConfigs can move to the
published images with the flow above. When the machine holds no protected
baseline from that installation, reconstruct the baseline files from live
state; the upgrade helper compares both against live Helm values and the
mounted Installation Secret before any mutation, so a mismatch fails safe.

```bash
helm get values oce -n openclaw-system -o yaml > "$OCC_INPUT_DIRECTORY/values.yaml"

oc -n openclaw-system get secret \
  "$(yq -er '.installation.secretName' "$OCC_INPUT_DIRECTORY/values.yaml")" -o json |
  jq -er '.data["installation.yaml"]' | base64 -d \
  > "$OCC_INPUT_DIRECTORY/installation.yaml"

chmod 600 "$OCC_INPUT_DIRECTORY/values.yaml" "$OCC_INPUT_DIRECTORY/installation.yaml"
```

Retrieve a service key through the bootstrap claim read Pod described in the
[install guide](install.md#retrieve-bootstrap-credentials-and-authenticate)
when none exists locally; set the Pod image to the installed controller digest
and the claim to `bootstrap.password.claimName` from the live values. Delete
the Pod as soon as the files are copied.

After the upgrade, verify every Agent that was running. `bin/occ agent get`
must show a new `activeRevisionId`, the Agent namespaces' PVC names and UIDs
must be unchanged, and a real model turn must complete inside each gateway
Pod. The runtime image bundles the OpenClaw CLI, so a turn needs no browser:

```bash
# One gateway Deployment per running Agent, named gateway-<gateway-id>.
oc -n "$GATEWAY_NAMESPACE" exec deploy/gateway-<gateway-id> -- \
  node /app/openclaw.mjs agent -m 'Reply with exactly: upgrade-ok' \
  --json --timeout 180
```

Require `"summary": "completed"` and the reply text in
`result.payloads[0].text`; an initialized session alone does not prove the
selected Harness reached the model.

## 2. Prepare the upgrade

For Keycloak human ingress, update each Agent's proxy before the new controller
requires `x-openclaw-scopes`. Keep the existing private login directory, cookie
and client secrets. Save that Agent's serving Configuration at `CONFIGURATION_FILE`,
then run `scripts/configure-team-login-scopes --configuration "$CONFIGURATION_FILE"`
with its team-login environment. It copies existing per-person grants into a
client-specific signed claim. Regenerate the proxy with `scripts/prepare-team-login`
and apply its protected manifests, preserving any reviewed Gateway settings.
The generated checksum rolls the proxy Pods; restart the Deployment explicitly
if updating only its Secret. See
[Keycloak login](team-login.md#2-prepare-realm-accounts-and-the-proxy) for the
full environment and preparation steps. Do not replace per-person caps with a
shared administrator header. A fresh Keycloak sign-in may be needed to refresh
an existing proxy session. Machine-only OCC ingress needs no Keycloak change.

Complete the selected OCE release's [upgrade checklist](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/upgrade-checklist.md)
before continuing. Compare protected inputs with live Helm values and the mounted
Installation Secret. Save a private baseline and back up PostgreSQL before
migrations; the dev database has no automatic backups. Back up Agent volumes
before runtime changes when recovery could require restoring state. Verify that
every running Agent supports the selected runtime and OpenClaw pin. For a trial
that needs a custom runtime, qualify the replacement image before redeploying it.
If intentionally discarding that trial, delete its Agent through OCC and wait for
cleanup before the upgrade. Preserve the remaining Agents' PVCs. Pause other operators' Helm changes through completion or
recovery.

If the existing installation predates October 13, 2026, read the selected
release's [OpenShell credential-source breaking change](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/breaking-changes-archive.md#2026-10-13-openshell-openai-credential-sources-need-replacing)
before cutover. Older `openai` sources can reference the former shared provider
profile. They need replacement sources, exact Agent grants, and updated Agent
bindings; an image upgrade alone does not repair those bindings. Preserve the
existing model Secrets and Agent PVCs.

Set your own protected OCC key:

```bash
export OCC_URL='https://<your OCC Console host>'

export OCC_SERVICE_KEY_FILE='/absolute/path/to/your-service-key.json'

export CONTEXT="$(oc config current-context)"
```

Bind the Installation Secret to the Installation returned by OCC. This annotation
is required by the upgrade helper. The command preserves an existing matching ID
and refuses to replace a different one:

```bash
export OCC_INSTALLATION_ID="$(bin/occ --output json installation get | jq -er .id)"

export OCC_INSTALLATION_SECRET="$(yq -er '.installation.secretName // "occ-installation-startup"' "$OCC_INPUT_DIRECTORY/values.yaml")"

existing_id="$(oc -n openclaw-system get secret "$OCC_INSTALLATION_SECRET" \
  -o go-template='{{ index .metadata.annotations "openclaw.dev/installation-id" }}')"

if test -z "$existing_id" || test "$existing_id" = '<no value>'; then
  oc -n openclaw-system annotate secret "$OCC_INSTALLATION_SECRET" \
    "openclaw.dev/installation-id=$OCC_INSTALLATION_ID"
elif test "$existing_id" != "$OCC_INSTALLATION_ID"; then
  echo 'Installation ID mismatch; stop and investigate before upgrading.' >&2
  false
fi
```

## 3. Upgrade the controller

Use a new evidence directory for each release. The helper creates it; do not
create it beforehand. OCC's API and worker stop during this command:

```bash
scripts/upgrade \
  --kubeconfig "$KUBECONFIG" --context "$CONTEXT" \
  --namespace openclaw-system --release oce \
  --values "$OCC_INPUT_DIRECTORY/values.yaml" \
  --installation "$OCC_INPUT_DIRECTORY/installation.yaml" \
  --controller-image "$CONTROLLER_IMAGE" \
  --source-revision "$OCC_REV" \
  --evidence-dir "$OCC_INPUT_DIRECTORY/upgrade-$OCC_REV-controller"
```

The helper preflights startup, stops writers, runs Helm migrations and checks
rollout. It neither creates backups nor proves model execution. Verify a real
model turn afterward and compare Agent revisions, PVC identities and workspace
hashes with the baseline.

The helper updates the `--values` and `--installation` files with the selected
images. Share those refreshed files with teammates after verification; older
copies will fail the next upgrade's live-state comparison.

For a runtime release, build and verify (or resolve the published) `runtime`,
then use `--runtime-image "$RUNTIME_IMAGE"` instead of `--controller-image`. Adding both image flags changes
both components. Check the upstream helper's current prerequisites for the
selected release, including repository brokers when enabled.

**Do not run `scripts/prepare-inputs`, repeat bootstrap-volume preparation, or
recreate system Secrets during an upgrade.** Those commands prepare a fresh
installation and reset routing fields. Baseline values and Installation files
must match live state; use separately reviewed candidate files for settings
changes. Preserve the dashboard's OpenShell OIDC overlay on later gateway
upgrades. OCC upgrades do not upgrade Keycloak or OpenShell automatically.

For a stopped or partially applied release, follow the upstream
[recovery procedure](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/production-upgrade-recovery.md)
instead of starting a second upgrade. No push or deployment runs automatically
when this repository or OCE main is fetched.

## Upgrade OpenShell separately

OpenShell has its own Helm release and SQLite PVC. Back up that database
consistently and retain its encryption/JWT Secrets before changing versions.
Keep the live values, including the dashboard OIDC overlay; applying only the
fresh-install values would discard that overlay. Agent PVCs and credentials stay
in place, but connections can pause while the gateway restarts.

This procedure selects OpenShell 0.1.6 and the Red Hat Konflux images. Review
the [v0.1.6 upgrade notes](https://github.com/NVIDIA/OpenShell/releases/tag/v0.1.6):

- Gateways refuse to start unless OIDC, mTLS, or operator authentication is
  configured, unless `server.auth.allowUnauthenticatedUsers` is `true`. The
  NetworkPolicy-bounded plaintext profile already sets it; do not drop it.
- New sandboxes open the control connection from the sandbox to its
  supervisor. Upgrade the supervisor and sandbox runtime images together;
  a new supervisor with an old sandbox runtime cannot create sandboxes.
  Running sandboxes keep their existing connection direction.
- Sandbox template Pod labels and annotations require separate operator
  allowlists (since 0.1.5). Merge the OCE label allowlist below. Allow any
  additional template annotation keys only after reviewing their use;
  OpenShell's reserved identity domains cannot be allowlisted.

The values file pins the gateway, supervisor, and sandbox runtime images to
the `quay.io/opendatahub` `odh-openshell-*` Konflux builds of the
`opendatahub-io/openshell` midstream, all from one `v0.1.6-rhaiv.N` release
tag. Pick the tag, resolve its digests through the Quay API, and record both
in the values file; never mix release trains between the three images. To
return to upstream images, pin the `ghcr.io/nvidia/openshell` digests for the
matching chart version instead.

Keep the current transport and authentication settings during this version
upgrade. The GitHub App authority and HTTPS/mTLS cutover need their own reviewed
Installation and Gateway changes.

Download and verify the selected charts using the
[OpenShell chart preparation](cluster.md#stage-openshell-separately), then run:

```bash
# REQUIRED: a new private directory for this OpenShell upgrade.
export OPENSHELL_UPGRADE_DIRECTORY="$OCC_INPUT_DIRECTORY/openshell-0.1.6-upgrade"

umask 077

install -d -m 700 "$OPENSHELL_UPGRADE_DIRECTORY"

helm get values openshell-gateway -n openshell-system -o yaml \
  > "$OPENSHELL_UPGRADE_DIRECTORY/values-before.yaml"

cp "$OPENSHELL_UPGRADE_DIRECTORY/values-before.yaml" \
  "$OPENSHELL_UPGRADE_DIRECTORY/values.yaml"

export OPENSHELL_IMAGE_PINS="$PWD/manifests/openshift/openshell-gateway-values.yaml"

yq -i '.gateway.image = load(strenv(OPENSHELL_IMAGE_PINS)).gateway.image |
  .sandboxRuntime.image = load(strenv(OPENSHELL_IMAGE_PINS)).sandboxRuntime.image |
  .supervisor.image = load(strenv(OPENSHELL_IMAGE_PINS)).supervisor.image |
  .gatewayConfig."openshell.drivers.kubernetes".allowed_pod_labels =
    (((.gatewayConfig."openshell.drivers.kubernetes".allowed_pod_labels // []) +
      load(strenv(OPENSHELL_IMAGE_PINS)).gatewayConfig."openshell.drivers.kubernetes".allowed_pod_labels) | unique)' \
  "$OPENSHELL_UPGRADE_DIRECTORY/values.yaml"

helm template openshell-gateway \
  "$OCC_INPUT_DIRECTORY/openshell-charts/helm-chart-0.1.6.tgz" \
  --namespace openshell-system --is-upgrade \
  --set agentSandbox.preflight.enabled=false \
  -f "$OPENSHELL_UPGRADE_DIRECTORY/values.yaml" \
  > "$OPENSHELL_UPGRADE_DIRECTORY/rendered.yaml"
```

The preflight override is for offline rendering only; keep the CRD preflight
enabled for the actual upgrade. Inspect the private render before applying it. Preserve the StatefulSet name,
ServiceAccount names, PVC claim template, and encryption/JWT Secret references.
Keep the live claim's storage class and size; the new chart's
`server.dataStorageClass` and `server.dataStorageSize` are not a way to change an
existing claim template. Confirm that the OIDC overlay and network isolation
remain present. An offline render cannot recover existing Secret contents.

```bash
helm upgrade openshell-gateway \
  "$OCC_INPUT_DIRECTORY/openshell-charts/helm-chart-0.1.6.tgz" \
  --namespace openshell-system \
  -f "$OPENSHELL_UPGRADE_DIRECTORY/values.yaml" \
  --wait --timeout 7m

oc -n openshell-system rollout status statefulset/openshell-gateway --timeout=5m
```

The Gateway rollout does not redeploy the Agent fleet. Existing Sandboxes retain
their supervisor and sandbox runtime images until replaced. Compare the rendered
0.1.6 workspace chart with the Installation's existing workspace resources; apply
any required changes through OCE's
[Installation-change procedure](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/production-installation-changes.md).
Deploy one canary Agent through OCC, then each intended Agent after its model turn
succeeds. Keep each Agent's existing Configuration, credential sources and PVCs.
Verify a model turn, tool credentials and the dashboard afterward. The CLI model
turn from the Agent Gateway Pod works for Codex-harness Agents; native-harness
Agents keep their model credential inside the sandbox, so verify those through
the Control UI. Share the refreshed protected gateway
values with maintainers separately from Secrets.
