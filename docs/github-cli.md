# Add GitHub CLI credentials to an OpenShell Agent

Use an OCC administrator account to attach a GitHub personal access token (PAT)
to an existing dedicated Codex Agent. The verified request is `gh api user` at
`api.github.com`; this does not configure authenticated git clone or push.
Choose a fine-grained PAT limited to the repositories and permissions needed.

This recipe is for ordinary Credential Gateway configurations. It is incompatible
with the repository-only Credential Proxy configuration used by
[protected GitHub App access](openshell-github-app.md); do not attach this extra
`bearer-token` source there. Use the App's approved repository bindings instead.

## 1. Enable tool credentials once per Installation

In the protected `installation.yaml`, add `/usr/local/bin/gh` to
`drivers.credential_gateway.configuration.toolBinaries`. Preserve `binaries`
and any existing tool entries. Verify the executable path in your runtime image.

```yaml
# Fragment only; retain the other Installation settings.
drivers:
  credential_gateway:
    configuration:
      toolBinaries:
        - /usr/local/bin/gh
```

Apply this through OCE's
[Installation-change procedure](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/production-installation-changes.md)
from the installed controller's source checkout. Keep its chart and image
references unchanged. This restarts OCC's API and worker; the Agent continues
serving until you redeploy it below. Without `toolBinaries`, the Credential
Gateway does not offer `bearer-token` sources.

## 2. Select the Agent and protected PAT file

Run from this setup repository with `bin/occ` already built and `OCC_URL`,
`OCC_NAMESPACE`, and `OCC_SERVICE_KEY_FILE` set for the Agent's OCC Namespace.
The source and Secret must belong to that same Namespace.

```bash
umask 077
set -o pipefail

# REQUIRED: existing Agent ID and a separate protected input directory.
export AGENT_ID='agt_REPLACE_ME'

export AGENT_INPUT_DIRECTORY="$HOME/.local/state/oce-agents/REPLACE_ME"

# REQUIRED: mode-0600 file containing only the PAT, not KEY=value.
export GH_PAT_FILE='/private/path/to/github-pat'

mkdir -p "$AGENT_INPUT_DIRECTORY"

chmod 700 "$AGENT_INPUT_DIRECTORY"

test -s "$GH_PAT_FILE"

bin/occ agent get "$AGENT_ID" -o json > "$AGENT_INPUT_DIRECTORY/before-github.json"
```

## 3. Register the Secret and credential source

These commands create new resources; run this section once per Agent/PAT.
The temporary request contains the PAT and is removed after registration.
OCC stores the credential in its managed Secret and OpenShell stores its provider
copy. Do not put the PAT in chat, command arguments or a checked-in file.

```bash
jq -n --rawfile token "$GH_PAT_FILE" \
  '{name:"github-pat", value:($token | sub("[\\r\\n]+$"; ""))}' \
  > "$AGENT_INPUT_DIRECTORY/github-secret-request.json"

export GH_SECRET_REF="$(bin/occ secret create \
  --file "$AGENT_INPUT_DIRECTORY/github-secret-request.json" -o json | jq -c .ref)"

test -n "$GH_SECRET_REF" && test "$GH_SECRET_REF" != null

rm "$AGENT_INPUT_DIRECTORY/github-secret-request.json"

jq -n --argjson ref "$GH_SECRET_REF" \
  '{name:"github-api", type:"bearer-token",
    config:{host:"api.github.com", env_var:"GH_TOKEN", path:"/**"},
    secrets:{token:$ref}}' > "$AGENT_INPUT_DIRECTORY/github-source.json"

export GH_SOURCE_ID="$(bin/occ credential-source create \
  --file "$AGENT_INPUT_DIRECTORY/github-source.json" -o json | jq -r .id)"
```

The Harness receives an opaque `GH_TOKEN` placeholder. OpenShell substitutes the
PAT on approved outbound requests from `gh` to the configured endpoint. Do not
inspect, echo or copy that placeholder into a conversation. Two attached sources
cannot use the same environment variable; check for an existing `GH_TOKEN` source
before registering another.

## 4. Grant and attach the source to this Agent

Grant its service principal permission to operate only this source.

```bash
export AGENT_PRINCIPAL="$(jq -r .servicePrincipalId "$AGENT_INPUT_DIRECTORY/before-github.json")"

jq -n '{name:"GitHub CLI credential access",
  permissions:[{action:"operate",resourceKind:"credential_source"}]}' \
  > "$AGENT_INPUT_DIRECTORY/github-role.json"

export GH_ROLE_ID="$(bin/occ iam role create \
  --file "$AGENT_INPUT_DIRECTORY/github-role.json" -o json | jq -r .id)"

jq -n --arg principal "$AGENT_PRINCIPAL" --arg role "$GH_ROLE_ID" --arg source "$GH_SOURCE_ID" \
  '{subjectKind:"identity",subjectId:$principal,roleId:$role,
    resourceKind:"credential_source",resourceId:$source}' \
  > "$AGENT_INPUT_DIRECTORY/github-binding.json"

bin/occ iam access-binding create --file "$AGENT_INPUT_DIRECTORY/github-binding.json"

bin/occ agent get "$AGENT_ID" -o json > "$AGENT_INPUT_DIRECTORY/before-github-attach.json"

jq --arg source "$GH_SOURCE_ID" '
  {configurationId,backendId,executionMode,plugins,pluginApprovers,harnessAuth,
   credentialSources:((.credentialSources // []) + [{sourceId:$source}] | unique_by(.sourceId))}
  | with_entries(select(.value != null))' \
  "$AGENT_INPUT_DIRECTORY/before-github-attach.json" \
  > "$AGENT_INPUT_DIRECTORY/attach-github.json"

bin/occ agent update "$AGENT_ID" --file "$AGENT_INPUT_DIRECTORY/attach-github.json"

export DEPLOYMENT_ID="$(bin/occ agent deploy "$AGENT_ID" -o json | jq -r .id)"

bin/occ agent deployment-status "$AGENT_ID" "$DEPLOYMENT_ID"
```

Expect `succeeded`; repeat the last status command until deployment finishes.
This preserves the existing model credential, Configuration and workspace PVC,
and restarts this Agent's runtime. Coordinate with anyone editing the Agent:
the update is based on its saved draft and must not overwrite concurrent changes.

## 5. Verify through the Agent

Ask in its Control UI:

```text
Run /usr/local/bin/gh api user --jq .login and report the actual username.
Use its existing environment. Do not run gh auth login or gh auth token,
inspect environment variables, print credentials or use another client.
```

Expect the PAT owner's GitHub username. This was verified through Shelley's
Codex/OpenShell tools with exit code `0`. A token's permissions still limit which
repositories and operations succeed. Other GitHub hosts and git transport have
not been qualified by this recipe.

The credential profile authorizes its configured endpoint for the approved tool;
a missing Agent network-policy addition alone does not establish a denial for
this credentialed request. Use the Python, Node or curl examples in the
[policy demo](policy-demo.md) for repeatable deny → approve demonstrations.

## Refresh dashboard data on demand

An HTML widget's browser requests do not inherit this OpenShell credential.
A public GitHub API request can therefore fail even when the Agent's authenticated
`gh api user` succeeds. Do not embed the PAT or the Harness placeholder in widget
HTML or browser storage.

For repository summaries without a supported authenticated dashboard binding,
ask the Agent to fetch data with `gh`, then replace the same pinned widget with a
timestamped snapshot. Its **Request fresh snapshot** button can use the supported
`openclaw.prompt.send` bridge to ask the Agent to fetch and update it again.
Refresh requires a click; do not poll, refresh on page load, or add background jobs.
Keep the last successful snapshot visible if a later request fails. This is an
Agent turn, not a direct browser fetch, and requires a connected Control UI.

Bound repository summaries at the API query: for example, 25 recently updated
open PRs, 10 recent merges and 10 related items. Request only displayed fields.
Label partial lists clearly and build author filters from the loaded subset.
Include the same limits in the widget’s refresh prompt; fetching every page and
then trimming the displayed list does not reduce the request cost.

When using REST query parameters with `gh api -f` or `-F`, specify `--method GET`
for read-only search: those flags otherwise select POST. Check real results before
reporting a credential or repository permission failure. An authenticated GraphQL
read is another option when supported by the token and endpoint policy.
