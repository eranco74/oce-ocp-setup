# Connect an OCE Agent to Slack on OpenShift

Enable both installation proxy paths, then configure Slack on your existing
Agent in OCC. The API proxy supports token validation and people/channel lookup;
the Gateway proxy supports messaging and Socket Mode. Configuring one does not
configure the other.

New installations use `scripts/prepare-inputs`, which prepares both paths.
For an existing installation, follow the steps below. They preserve controller
and runtime image references, database and Agent volumes, credentials, routing,
and admin-launcher settings. The control-plane rollout interrupts OCC requests;
deploying an affected Agent can interrupt its connections and turns.

Apply `runtime.channels` before the next OCE image upgrade, not during it:
releases from the 2026-11-02 carry images refuse to deploy an Agent whose
`channels.slack` is enabled while the Installation has no channel runtime, and
an Installation correction made mid-upgrade blocks the upgrade helper's resume.
See [upgrades](upgrades.md#2-prepare-the-upgrade).

## Prepare the existing installation

Run from this setup repository with `OCE_CHECKOUT` at the source revision of the
installed controller, its matching workspace dependencies, and the existing
protected `OCC_INPUT_DIRECTORY`. Use the dedicated kubeconfig for this cluster.
Require the protected inputs to match the live installation before editing;
see OCE's [baseline comparison](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/upgrade-baseline.md).
Do not resolve new images or rerun the initial installation helper.

```bash
set -euo pipefail
: "${OCE_CHECKOUT:?Set the checkout matching the installed controller}"
: "${OCC_INPUT_DIRECTORY:?Set the existing private installation input directory}"
: "${KUBECONFIG:?Set the dedicated target-cluster kubeconfig}"

oc whoami
oc whoami --show-server
oc config current-context
```

Confirm that these identify the intended cluster and administrator. The managed
proxy needs public IPv4 HTTPS egress; cluster-wide egress restrictions must also
permit it. The proxy restricts CONNECT requests to Slack hosts and excludes
private and reserved destination addresses.

Back up both inputs once, then prepare the settings:

```bash
umask 077
test ! -e "$OCC_INPUT_DIRECTORY/before-slack"
install -d -m 700 "$OCC_INPUT_DIRECTORY/before-slack"
cp "$OCC_INPUT_DIRECTORY/values.yaml" "$OCC_INPUT_DIRECTORY/before-slack/values.yaml"
cp "$OCC_INPUT_DIRECTORY/installation.yaml" "$OCC_INPUT_DIRECTORY/before-slack/installation.yaml"

scripts/prepare-slack

scripts/validate-installation "$OCC_INPUT_DIRECTORY/installation.yaml"

helm template oce "$OCE_CHECKOUT/deploy/helm/openclaw-enterprise" \
  --namespace openclaw-system -f "$OCC_INPUT_DIRECTORY/values.yaml" \
  > "$OCC_INPUT_DIRECTORY/oce-slack-rendered.yaml"
```

Review the private input diffs and rendered resources locally. Do not share
rendered inputs or Secret values. The helper enables `slackProxy`, uses its
Service name and port for `runtime.channels.proxyUrl` and `managedProxy`, and
updates `controlPlane.installationChecksum`. It refuses an existing external
directory proxy so that selecting the managed proxy requires a separate review.
It changes no cluster resources.

## Apply the installation change

Grant the managed proxy ServiceAccount the existing fixed-UID SCC. Patch only
the Installation Secret's document key to preserve its ownership annotation.
These commands assume the recipe's Helm release `oce` in `openclaw-system`.

On a cluster bootstrapped with `scripts/bootstrap-argocd`, the admission stage
owns the Role and RoleBinding in the first command; skip that `oc apply` and
let Argo reconcile it. See [the GitOps guide](gitops.md).

```bash
oc apply -f manifests/openshift/base/admission/control-plane-scc-rbac.yaml

export OCC_INSTALLATION_SECRET="$(yq -er '.installation.secretName // "occ-installation-startup"' "$OCC_INPUT_DIRECTORY/values.yaml")"
export OCC_INSTALLATION_KEY="$(yq -er '.installation.key // "installation.yaml"' "$OCC_INPUT_DIRECTORY/values.yaml")"
jq -n --arg key "$OCC_INSTALLATION_KEY" \
  --rawfile document "$OCC_INPUT_DIRECTORY/installation.yaml" \
  '{data: {($key): ($document | @base64)}}' |
  oc -n openclaw-system patch secret "$OCC_INSTALLATION_SECRET" \
    --type merge --patch-file /dev/stdin

helm upgrade oce "$OCE_CHECKOUT/deploy/helm/openclaw-enterprise" \
  --namespace openclaw-system -f "$OCC_INPUT_DIRECTORY/values.yaml" \
  --wait --timeout 7m

oc -n openclaw-system rollout status deployment/openclaw-enterprise-slack-proxy --timeout=3m
oc -n openclaw-system rollout status deployment/openclaw-enterprise-api --timeout=3m
oc -n openclaw-system rollout status deployment/openclaw-enterprise-worker --timeout=3m
```

The checksum rolls out OCC's API and worker so they load the Gateway proxy
configuration. This does not deploy Agents. If the change fails, restore both
inputs from `before-slack`, recompute `controlPlane.installationChecksum` from
the restored Installation file, and repeat the Secret patch and Helm upgrade.
Follow OCE's [Installation change procedure](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/production-installation-changes.md)
for startup-check failures and recovery. Preserve the backups until verification
is complete.

## Configure and verify your Agent

1. In OCC, open the existing Agent's **Create new version → Channels → Configure
   Slack**. Slack requires Dedicated execution; native OpenClaw also needs the
   installation's supported native-worker and Sandbox configuration.
2. Select or create same-Namespace Secrets for the bot token (`xoxb-`) and app
   token (`xapp-`). Keep the tokens out of Helm values and Installation YAML.
3. Search for a known channel and person visible to the bot. Successful results
   verify the API proxy path. Exact channel IDs do not bypass token validation.
4. Enable Slack, select the channel and allowed people, and keep **Require a
   mention** enabled. Set direct messages separately. Save the Configuration.
5. Invite the bot to the selected Slack channel and choose **Deploy new version**
   in OCC. Existing running Slack Agents need new versions after the Gateway
   proxy change; restarting OCC does not update their workloads. Preserve
   stopped Agents' state.
6. Require the new Gateway and Harness to be Ready. Run diagnostics for that
   version, then send an explicit mention from an allowed real Slack user and
   require a reply containing a unique phrase. Directory results, stored
   credentials and diagnostics alone do not prove message delivery.

Before handoff, verify that the proxy denies an unrelated public host and a
private destination from the same permitted caller path. Follow the owning
[Slack integration guide](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/integrations/slack.md)
for app scopes, Socket Mode, optional direct messages and proxy verification.

If the proxy Pod fails SCC admission, check its exact ServiceAccount grant.
If lookup still fails, inspect API and proxy connectivity. A missing-scope error
requires updating Slack bot scopes and reinstalling the Slack app. A connected
Socket Mode gateway does not establish that OCC directory lookup works.
