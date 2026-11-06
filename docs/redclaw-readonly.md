# Give RedClaw read-only OCE cluster access

Let the RedClaw Slack Agent answer questions about the OCE stage cluster by
reading the OpenShift API from its own OpenShell sandbox. This recipe adds a
dedicated ServiceAccount with read-only RBAC across the OCE namespaces, a
curl-based helper and kubeconfig in the Agent's workspace, and one sandbox
network-policy addition for the in-cluster Kubernetes Service.

The trust boundary is the Slack channel: anyone who can mention RedClaw
effectively holds this read-only credential. The scope is exactly the OCE
namespaces, Secrets and ConfigMaps are excluded, and every write verb is
denied by RBAC and refused by the helper. Pod logs, including in the control
plane namespaces, are readable because they are part of the same boundary.

All pieces are additive and independently removable: the ClusterRole and
RoleBindings, the ServiceAccount and token Secret, the two workspace files,
and the `redclaw-readonly-kube` policy addition. Remove the cluster objects
with `oc delete` and the workspace files in the OCC UI. The policy addition
is removed through OCC. Using the section 1 inputs, from the repository root:

```bash
bin/occ agent policy get "$OCC_AGENT_ID" \
  --namespace "$OCC_NAMESPACE_ID" -o json |
  jq '{expectedVersion: .policy.version,
    additions: [.policy.additions[] |
      select(.id != "redclaw-readonly-kube")]}' \
  >"$OCC_INPUT_DIRECTORY/policy-rollback.json"

export REVISION_ID="$(bin/occ agent get "$OCC_AGENT_ID" \
  --namespace "$OCC_NAMESPACE_ID" -o json | jq -er .activeRevisionId)"

export POLICY_VERSION="$(bin/occ agent policy update "$OCC_AGENT_ID" \
  --namespace "$OCC_NAMESPACE_ID" \
  --file "$OCC_INPUT_DIRECTORY/policy-rollback.json" \
  -o json | jq -er .version)"

bin/occ agent policy apply "$OCC_AGENT_ID" \
  --namespace "$OCC_NAMESPACE_ID" \
  --revision "$REVISION_ID" \
  --expected-version "$POLICY_VERSION"
```

This leaves the policy's other additions untouched, and changes neither OCC
nor the Agent image.

Pinned names, used verbatim below:

- ClusterRole and RoleBinding `oce-redclaw-readonly`.
- ServiceAccount `redclaw-readonly` with token Secret
  `redclaw-readonly-token`, in RedClaw's tenant namespace. The scripts
  discover that namespace from Agent ServiceAccount `agent-c66e33c482ff`.
- Helper `kuberead` and sandbox policy addition `redclaw-readonly-kube`.

Scope: `openclaw-system`, `openclaw-postgres`, `openshell-system`,
`openshell-auth`, `agent-sandbox-system`, `envoy-gateway-system`, plus every
namespace labeled `openclaw.dev/namespace`.

## 1. Select the cluster and set the private inputs

An authorized operator runs this recipe from the repository root, with
`bin/occ` built by `scripts/build-occ` from the installed OCE source. Use a
dedicated kubeconfig for the stage cluster.

```bash
set -euo pipefail

# REQUIRED: dedicated kubeconfig for the OCE stage cluster.
export KUBECONFIG='/private/path/to/oce-stage-kubeconfig'

# REQUIRED: private input directory outside the repository.
export OCC_INPUT_DIRECTORY="${OCC_INPUT_DIRECTORY:-$HOME/.local/state/openclaw-enterprise/openshift-test}"

# REQUIRED: OCC Console origin and service key, as in the Agent recipes.
export OCC_URL='https://<OCC_CONSOLE_HOST>'
export OCC_SERVICE_KEY_FILE="$OCC_INPUT_DIRECTORY/initial-admin-service-key.json"

# Fixed defaults for RedClaw; override only for another Agent.
export OCC_AGENT_ID='agt_9fae6206-cef6-4cb9-bc1f-cbc3904967a9'
export OCC_NAMESPACE_ID='ns_8acb193b-f55f-4b6a-9be8-c748a744a505'

oc whoami
oc whoami --show-server
test -s "$OCC_SERVICE_KEY_FILE"
test -x bin/occ
```

Confirm that the server and identity are the stage cluster and an authorized
operator. `OCC_URL` and `OCC_SERVICE_KEY_FILE` follow the
[first Agent recipe](agent.md); keep the service key private and out of this
repository. RedClaw must already be deployed and answering in Slack. This
recipe gives that existing Agent cluster visibility; it does not create the
Agent.

## 2. Grant read-only RBAC

The grant script discovers RedClaw's tenant namespace from the Agent
ServiceAccount, applies the ClusterRole, creates the ServiceAccount and its
long-lived token Secret, then binds the ServiceAccount in every scope
namespace. It is idempotent and safe to re-run.

```bash
oc whoami --show-server

scripts/grant-redclaw-readonly
```

Expect the discovered tenant (RedClaw's is `oce-2e748b6a74018ff`), one
`granted read:` line per scope namespace, the two impersonation self-checks,
and the namespace count:

```text
Tenant namespace: <RedClaw tenant namespace>
...
granted read: agent-sandbox-system
granted read: envoy-gateway-system
granted read: <RedClaw tenant namespace>
Self-check: yes list pods -n openclaw-system, no get configmaps -n <tenant>
Granted read access in <N> namespaces.
```

The last line reports the actual count.

ConfigMaps are excluded on purpose: tenant `cfg-*` ConfigMaps carry full
Agent configuration. Secrets are excluded too. If the script fails while
matching the Agent ServiceAccount, check `AGENT_SA` and the tenant's
`openclaw.dev/namespace` label before retrying.

## 3. Prepare the private files

The prepare script reads the populated token Secret and the Kubernetes
Service ClusterIP, then writes four files under
`$OCC_INPUT_DIRECTORY/redclaw-readonly/`.

```bash
test -n "$OCC_INPUT_DIRECTORY"
test -n "$OCC_URL"
test -n "$OCC_SERVICE_KEY_FILE"

scripts/prepare-redclaw-readonly
```

Expect a SHA-256 line for `kubeconfig`, `kuberead`,
`agents-md-fragment.md` and `policy-update.json`. The `kubeconfig` is mode
0600 and contains a live token: keep the directory private, and never commit
or paste these files.

Review `policy-update.json` locally. It must keep every existing addition
(for example the GitHub egress suggestion) and append the
`redclaw-readonly-kube` entry granting `/usr/bin/curl` access to the
Kubernetes Service ClusterIP on port 443. If the script reports an empty
token, wait for the ServiceAccount token controller to populate
`redclaw-readonly-token`, remove the output directory, and re-run.

## 4. Upload the Agent workspace files

In OCC, open RedClaw's **Workspace files** tab and upload `kuberead` and
`kubeconfig` from `$OCC_INPUT_DIRECTORY/redclaw-readonly/` into the workspace
root that the sandbox exposes as `/sandbox/enterprise`. The helper reads
`kubeconfig` next to itself by default.

Open the Agent's `AGENTS.md` and append the contents of
`agents-md-fragment.md`; create `AGENTS.md` with that fragment when it does
not exist. Then deploy a new Agent version so the sandbox receives the files.
A running version does not see uploads made after its deployment.

Verify that the Agent can read both files from `/sandbox/enterprise` during a
turn. OCC's CLI has no workspace-file command, so this step is UI-only.
Upload the `kubeconfig` file itself; do not paste its contents into a
conversation.

## 5. Apply the sandbox network policy

The addition is scoped to `/usr/bin/curl` and a single destination port: the
in-cluster `kubernetes` Service on 443. OCC stores policy per Agent and
applies it to one exact running revision.

Save the additions, then apply the saved version to the active revision:

```bash
export REVISION_ID="$(bin/occ agent get "$OCC_AGENT_ID" \
  --namespace "$OCC_NAMESPACE_ID" -o json | jq -er .activeRevisionId)"

export POLICY_VERSION="$(bin/occ agent policy update "$OCC_AGENT_ID" \
  --namespace "$OCC_NAMESPACE_ID" \
  --file "$OCC_INPUT_DIRECTORY/redclaw-readonly/policy-update.json" \
  -o json | jq -er .version)"

bin/occ agent policy apply "$OCC_AGENT_ID" \
  --namespace "$OCC_NAMESPACE_ID" \
  --revision "$REVISION_ID" \
  --expected-version "$POLICY_VERSION"
```

Expect the update response to report the new saved version, and the apply
response to report a pending application. Confirm enforcement, waiting until
the application state is `applied`:

```bash
bin/occ agent policy get "$OCC_AGENT_ID" \
  --namespace "$OCC_NAMESPACE_ID" -o json |
  jq '{version: .policy.version, application: .policy.application.state,
    addition: [.policy.additions[] |
      select(.id == "redclaw-readonly-kube")]}'
```

Expect `version` equal to `POLICY_VERSION`, the addition showing the
Kubernetes Service ClusterIP, and `application` equal to `applied`. A pending
or failed application is not enforcement evidence. After you deploy a later
Agent version, refresh `REVISION_ID` and re-run the `apply` step against that
revision and saved version.

If OCC rejects the IP-literal host, use the Service DNS name instead. Remove
`$OCC_INPUT_DIRECTORY/redclaw-readonly`, re-run
`scripts/prepare-redclaw-readonly` to rebuild the files and refresh the
policy version, then change `host` in `policy-update.json` and the `server:`
line in `kubeconfig` to `https://kubernetes.default.svc:443` before
re-uploading and applying again. From the sandbox, confirm that
`kubernetes.default.svc` resolves before retrying the read.

## 6. Verify

Check the RBAC boundary directly with impersonation:

```bash
export TENANT_NAMESPACE="$(oc get namespaces \
  -l "openclaw.dev/namespace=$OCC_NAMESPACE_ID" -o json |
  jq -er '.items | if length == 1 then .[0].metadata.name
    else error("expected one tenant namespace") end')"

export READONLY_SA="system:serviceaccount:$TENANT_NAMESPACE:redclaw-readonly"

oc auth can-i list pods -n "$TENANT_NAMESPACE" --as="$READONLY_SA"
oc auth can-i list pods -n openclaw-system --as="$READONLY_SA"
oc auth can-i get pods/log -n openclaw-system --as="$READONLY_SA"
oc auth can-i get configmaps -n "$TENANT_NAMESPACE" --as="$READONLY_SA"
oc auth can-i get secrets -n "$TENANT_NAMESPACE" --as="$READONLY_SA"
oc auth can-i list pods -n default --as="$READONLY_SA"
oc auth can-i create deployments -n "$TENANT_NAMESPACE" --as="$READONLY_SA"
```

Expect `yes` for:

- `list pods` in the tenant namespace and in `openclaw-system`.
- `get pods/log -n openclaw-system`.

Expect `no` for:

- `get configmaps` and `get secrets` in the tenant namespace.
- `list pods -n default`.
- `create deployments` in the tenant namespace.

Then run real Slack turns in the channel that can reach RedClaw:

1. Ask RedClaw to list the Pods in its namespace and their status. Expect an
   answer read through `kuberead`; an empty namespace is still a valid read.
2. Ask it to delete or scale one of those Pods. Expect a refusal that states
   the access is read-only, with no API write.
3. Ask about a namespace outside OCE, such as `default`. Expect an empty or
   forbidden result with no workaround.

Do not accept a general statement about permissions as proof. Require the
observed read result and the refused write.

## 7. Rotate the credential

The kubeconfig embeds a token. Replace the Secret and the workspace file;
the ServiceAccount and RBAC grants stay unchanged. Using the
`TENANT_NAMESPACE` from section 6:

```bash
rm -r "$OCC_INPUT_DIRECTORY/redclaw-readonly"

oc -n "$TENANT_NAMESPACE" delete secret redclaw-readonly-token

scripts/grant-redclaw-readonly
```

Wait until the new token is populated, then rebuild and re-upload the files:

```bash
until oc -n "$TENANT_NAMESPACE" get secret redclaw-readonly-token \
  -o jsonpath='{.data.token}' | grep -q .; do sleep 5; done
printf 'token ready\n'

scripts/prepare-redclaw-readonly
```

Upload the new `kubeconfig` in the **Workspace files** tab and deploy a new
Agent version. `kuberead` and `agents-md-fragment.md` are unchanged, so only
the kubeconfig needs replacing. Re-run the section 5 `apply` step if the new
Agent version needs the policy application again.

## 8. Add a tenant

When a new tenant namespace appears and carries the
`openclaw.dev/namespace` label, widen RedClaw's read scope explicitly:

```bash
scripts/grant-redclaw-readonly
```

Nothing else changes: the token, kubeconfig, workspace files and policy
addition already cover the same API endpoint. Re-run the impersonation
checks from section 6 when you want proof for the new namespace.
