# Deploy OCE on OpenShift: the operator recipe

For existing Claws, [generate a private Claw links directory](claw-links.md)
to share human login links with your team.

Run the six steps below from the repository root. Each linked page contains
command boxes to run in order; finish that page before continuing here.
Use **dedicated Codex through OpenShell**. This repository owns deployment inputs only; OCE application code stays in its
separate source checkout.
The procedure was exercised on OpenShift 4.22, two `amd64` workers,
AWS EBS `gp3-csi`, and enforcing NetworkPolicies.

For an existing installation, use the [image upgrade procedure](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/production-upgrade.md)
instead of repeating bootstrap. Verify that the selected OpenClaw runtime can
read the Agent's existing database schemas before replacing it; the stock pin
can be older than a previously selected upstream build. Preserve state rather
than lowering its schema version. The OpenShift API egress example also selects
the upgrade preflight Pods so they can reach the translated API endpoint port.

## Follow the guides in order

Start logged in to the intended cluster as an administrator. Commands use your
current kubeconfig context. You need `oc`, Helm, `jq`, `yq` v4, Git, Go, Python 3.12 or later,
OpenSSL, `curl`, `envsubst`, Node.js 24 or later, and the OCE checkout’s installed
workspace dependencies.
Keep credentials and generated files outside both repositories. Set
`OCE_CHECKOUT` to a dedicated, clean OCE clone as shown in the root README.

**Set your private directory and bootstrap email.**

```bash
umask 077

# OPTIONAL: change this to another user-owned directory outside the checkout.
export OCC_INPUT_DIRECTORY="$HOME/.local/state/openclaw-enterprise/openshift-test"

install -d -m 700 "$OCC_INPUT_DIRECTORY"

# OPTIONAL: replace this email before the first OCC install.
export OCC_ADMIN_EMAIL='occ-admin@example.test'

oc whoami

oc whoami --show-server
```

**1. [Prepare the cluster](cluster.md).**

Replace `CONTROL_NODE` and `AGENT_NODE` with two different ready `amd64` node
names. Run all sections, including **Stage OpenShell separately**. The Console
hostname is discovered automatically. Finish with cert-manager and OpenShell
ready and the private Console Route file prepared.

**2. [Build the images and install OCC](install.md).**

Build the unmodified OCE main revision using its standard OpenClaw runtime pin.
Wait for both builds to finish, or
[use the published images](install.md#use-the-published-images-instead-of-building)
and skip the build. Image digests, network addresses, and cluster
identity are discovered by the commands. Configure the Codex OpenShell workspace,
create Secrets, install OCC, grant tenant permissions, and retrieve bootstrap
credentials. Finish with OCC healthy and the default Namespace `ready`.

**3. [Configure private Agent routing](routing.md).**

Install Envoy, enable routing, and complete the linked tenant attachment
procedure when instructed. Return to verify readiness before creating an Agent.

**4. [Create and deploy the Agent](agent.md).**

Enter your authorized OpenAI API key at the hidden prompt; do not paste it into
a Configuration file. Use the Codex Configuration, then run credential grants, image-pull, SCC, and
deployment commands.
Replace the revision-ID placeholder with the ID returned by deployment.
Finish with deployment status `succeeded`.

**5. [Install the Control UI Route and sign in](control-ui.md).**

This required step creates the OAuth-protected Route for the Codex Agent and
enables the Control UI for its exact origin. For a non-admin user, replace `CONTROL_UI_USER` with their existing OpenShift
username and apply the exact Service grant. Skip that grant for an admin-only
trial. Deploy the updated Configuration, open the printed URL, sign in through
OpenShift OAuth, and send a request. A completed assistant response is the finish
line; a healthy Pod or connected browser alone is insufficient.

**6. [Enable the OCC admin launcher](native-admin.md).**

Complete the global Helm enablement once, then prepare the exact HTTPS admin
endpoint and Configuration origin for this Agent. Sign back into OCC, select
**Refresh access**, and require **Open admin console** to open a connected Control
UI. The OpenShift OAuth Route in step 5 does not enable this button. Keep both
entry points: direct user access and OCC-authorized administration.

## Create additional Agents

To connect an existing Agent to Slack, follow [Slack setup](slack.md). It enables
both installation proxy paths before configuring the Agent in OCC. To give an
existing Agent read-only OCE cluster visibility, follow
[RedClaw read-only cluster access](redclaw-readonly.md).

After deploying OCC once, follow the [additional Agent recipe](agents.md).
Each Codex Agent gets its own Namespace and Control UI Route. All Agents use
the Installation’s existing images.

Administrators create the drafts and infrastructure grants. To let a non-admin
owner edit and deploy an existing Agent, follow [Agent owner access](owner-access.md).

To inspect the private OpenShell gateway, use the optional
[OpenShell dashboard recipe](dashboard.md). It adds a separate
Keycloak login without changing OCC or Agent sign-in.

## Agent task brief

Agents follow the same recipe. The operator enters the API key locally and runs
each Agent deployment command. Do not print credentials or commit private inputs.
Report the active revision and real model-turn result; do not claim untested
Codex or non-admin browser behavior.
