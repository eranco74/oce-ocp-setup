# OCE setup on OpenShift

Deployment recipes, Helm inputs and Kustomize manifests for OpenClaw
Enterprise (OCE) on OpenShift. This repository contains no OCE application code.
It supports dedicated Codex Agents through OpenShell and an optional
Keycloak-protected OpenShell dashboard.

## Start here

Looking for your Claw? [Generate a private Claw links directory](docs/claw-links.md)
to share with your team.

Use a separate OCE checkout. Keep it on the release you intend to build or deploy;
fetching main selects a candidate, not permission to upgrade a running cluster.

```bash
# REQUIRED: absolute path to your OCE source checkout.
export OCE_CHECKOUT='/absolute/path/to/openclaw-enterprise'

# Run commands from this setup repository root.
scripts/build-occ
```

- **Fresh cluster:** [cluster preparation](docs/cluster.md), then [OCC installation](docs/install.md).
- **First Agent:** [private routing](docs/routing.md), [Agent creation](docs/agent.md), and [Control UI](docs/control-ui.md).
- **Another Agent:** [additional-Agent recipe](docs/agents.md), using the existing images.
- **Native OpenClaw:** [dedicated Agent through OpenShell](docs/native-harness.md), using the standard pinned runtime; no PR #1982 build is required.
- **Shared Agent:** [custom-model team Agent recipe](docs/shared-agent.md), with shared sessions and Gateway administration.
- **Experimental team identities:** [Keycloak member login](docs/team-login.md), requiring [OCE PR #3049](https://github.com/openclaw/openclaw-enterprise/pull/3049), which is open and not yet merged.
- **OpenShell policy demo:** [deny, approve and retry](docs/policy-demo.md), available on OCE main after PR #2599 merged.
- **GitHub CLI:** [attach a PAT to one Agent](docs/github-cli.md).
- **Slack:** [enable the installation proxies and connect an Agent](docs/slack.md).
- **Personal Keycloak login:** [reuse an existing account for another Agent](docs/agent-keycloak-login.md).
- **Scoped owners:** [owner access](docs/owner-access.md); cluster-admin access remains an explicit alternative.
- **Dashboard:** [Keycloak and dashboard recipe](docs/dashboard.md).
- **OCC admin launcher:** [required setup for each Agent](docs/native-admin.md); enable globally once, then add each Agent's HTTPS endpoint and origin.
- **Dashboard widgets:** [MCP Apps and widget preview access](docs/widgets.md).
- **Updating OCE:** [build and upgrade workflow](docs/upgrades.md).
- **Authenticated OpenShell management:** [mTLS cutover and renewal](docs/openshell-mtls.md), preserving dashboard OIDC.
- **Manifest layout:** [Kustomize stages](docs/kustomize.md).
- **Team Claw proposal:** [collaboration and identity plan](docs/team-claw-plan.md), pending agreement and implementation.

## Container images

By default the controller and runtime images are built inside the cluster ([build step](docs/install.md#build-the-two-images-inside-openshift)). To consume the published `quay.io/aipcc/base-images/agentic/oce-controller` and `oce-runtime` images instead, `scripts/resolve-images` pins them to `linux/amd64` digests and the build is skipped: see the [install](docs/install.md#use-the-published-images-instead-of-building) and [upgrade](docs/upgrades.md#use-the-published-images-instead-of-building) steps. The published images are built from OCE main, so recipes that need unmerged OCE changes still need a build.

## Repositories and private state

`manifests/openshift/` contains cluster and Agent assets. `manifests/dashboard/`
contains the optional dashboard and identity-provider assets. `scripts/` prepares
inputs or invokes the selected OCE checkout's tools. OCE owns its Helm chart,
production examples, startup validator, migrations, bootstrap-volume helper and
upgrade workflow; use those from `OCE_CHECKOUT` rather than vendoring them here.

Keep kubeconfigs, credentials, rendered cluster inputs, source archives and
verification evidence outside both repositories. Share a configuration-only
bundle internally; supply secrets separately. Neither build commands nor fetching
main should silently alter installed images. Record source commits, chart versions
and image digests for every release.

The recipes were exercised on OpenShift 4.22.17, Kubernetes 1.35.8 and amd64,
with OpenShell 0.1.4, Crunchy 5.8.9 and single-instance PostgreSQL without backups.

The recipes now select OpenShell 0.1.5; the cluster upgrade and model-turn
verification are pending. Follow [Upgrade OpenShell](docs/upgrades.md#upgrade-openshell-separately)
for the version change.
This is a development setup, not a production qualification. Dashboard OIDC is
separate from OCC/Agent login. Existing OpenShell private-network authentication
remains development-only and must retain its network isolation.
