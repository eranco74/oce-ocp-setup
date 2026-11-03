# Add upstream repository candidates

Prepare these repository choices for the OCE UI:

| Repository                    | Contents | Pull requests | Issues |
| ----------------------------- | -------- | ------------- | ------ |
| `openclaw/openclaw-enterprise` | Read     | Write         | Write  |
| `openclaw/openclaw`            | Read     | Write         | Write  |

Metadata is read-only. Pull-request and issue writes allow the Agent to create
and update those resources; they are not read-only grants. Code changes must be
pushed to a separately authorized fork. GitHub permissions and repository rules
still apply. No workflow-write or administration permission is requested.

The [candidate template](../manifests/openshift/openshell-git/upstream-repositories.json)
records this policy without App IDs, installation IDs, Namespace IDs or credentials.
It is not a complete registry or a Kubernetes resource. It is deliberately absent
from the OpenShell Git Kustomization; merging this setup PR does not change live
access or populate the UI.

## Check the installed release and GitHub App scope first

The [protected Git recipe](openshell-github-app.md) records the exercised release
and its limits. In that release:

- The private repository registry supplies the UI's candidate list. Each entry
  must authorize the intended OCE Namespace. Selecting a candidate and deploying
  the Agent are separate steps.
- One registry names one GitHub App installation. An installation owned by
  `rh-oce-team` cannot issue a token for repositories owned by `openclaw`. An
  authorized operator in `openclaw` must install or approve the App for both
  upstream repositories with the permissions above. Public visibility alone
  does not admit a repository through protected Git.
- Repositories selected together with `github-repository` must share the same
  permission map. These upstream grants cannot be selected beside the current
  team-fork grants with Contents write. Do not raise upstream Contents to write
  or reduce existing fork permissions to make admission pass.

These are rollout prerequisites, not changes this template implements. If the
current Installation cannot provide the required upstream App installation and
permission grouping, stop before rollout. Same-Agent fork writes plus upstream
reads and PR/issue writes need a supported OCE configuration or an OCE product
change; adding names to the registry does not supply it. Do not replace the
working team registry or add a general GitHub network allowance as a workaround.

## Prepare a private registry candidate

Proceed only for a registry whose App installation has verified access to both
upstream repositories. Obtain their numeric repository IDs from an authorized
GitHub reader. Use the actual OCE Namespace ID, not an OpenShift namespace name.
From this setup repository root, set:

- `CURRENT_REPOSITORY_REGISTRY`: absolute path to the current private registry.
- `OCC_INPUT_DIRECTORY`: existing private output directory outside both checkouts.
- `OCC_NAMESPACE`: the authorized OCE Namespace ID.
- `UPSTREAM_OCE_REPOSITORY_ID`: numeric ID of `openclaw/openclaw-enterprise`.
- `UPSTREAM_OPENCLAW_REPOSITORY_ID`: numeric ID of `openclaw/openclaw`.

The following command retains existing registry settings and entries, rejects
duplicate candidates, and creates a new private file without overwriting one:

```bash
umask 077
python3 - <<'PY'
import json
import os
from pathlib import Path
from string import Template

source = Path(os.environ["CURRENT_REPOSITORY_REGISTRY"])
output = Path(os.environ["OCC_INPUT_DIRECTORY"]) / "registry-upstream-candidate.json"
if not source.is_absolute() or not output.is_absolute():
    raise SystemExit("Use absolute private input and output paths.")
registry = json.loads(source.read_text())
template = Path("manifests/openshift/openshell-git/upstream-repositories.json")
entries = json.loads(Template(template.read_text()).substitute(os.environ))
for key in ("repositoryRef", "repositoryId", "repository"):
    values = [entry[key] for entry in registry["repositories"] + entries]
    if len(values) != len(set(values)):
        raise SystemExit(f"Duplicate {key}; inspect the current registry instead of replacing it.")
registry["repositories"].extend(entries)
with output.open("x", encoding="utf-8") as handle:
    json.dump(registry, handle, indent=2)
    handle.write("\n")
print("Prepared a private registry candidate; no cluster changes were made.")
PY
```

Keep the rendered registry private. Validate it with the selected OCE release's
registry loader and Installation procedure. Verify App installation membership,
repository IDs and permissions on the trusted operator side before requesting
tokens. Rendering the template does not prove GitHub will authorize the grant.

## Roll out and verify after approval

Use the existing [cutover procedure](openshell-github-app.md#3-cut-over-in-dependency-order).
Create a new immutable, versioned registry ConfigMap and update the existing
private Helm and Installation references together. Preserve all unrelated
entries, signing and custody Secrets, Agent identities and PVCs. Review the
candidate and obtain deployment authorization before changing the cluster.

Refresh repository choices in OCE and confirm both upstream candidates appear
for the intended Namespace. Select only a supported permission group, preserve
model and channel settings, and deploy the Agent. Existing revisions do not gain
the new repository access; their original grant deadlines and current
authorization checks still apply. Removing a binding can withdraw access before
that deadline or runtime retirement.

Through that Agent, verify `git ls-remote` and API reads of repository metadata,
open PRs and issues for both upstreams. Confirm an unselected repository remains
denied. Read success does not prove write permission: exercise PR/issue writes
only against an explicitly authorized target, and record any unverified writes.
Do not push to upstream as a verification step. Record the new grant deadline
and complete the protected Git recipe's model and identity checks.
