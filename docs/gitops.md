# Deploy OCE with Argo CD (GitOps)

This guide adds a GitOps path on top of the runbooks. Argo CD reconciles the
declarative platform stages from this repository; everything whose inputs are
private, secret-bearing, or part of the OCE release lifecycle stays a runbook
step. The hand-run recipe in [the deployment guide](README.md) still works on a
cluster without Argo CD.

## What Argo owns

| Owned by Argo CD                          | Stays a runbook step                                  |
| ----------------------------------------- | ----------------------------------------------------- |
| `operators`, `database`, `admission`, `openshell`, `routing` Kustomize stages | `control-plane`, `control-ui`, `control-ui-access` |
| The OpenShift GitOps operator Subscription, controller RBAC, AppProject | The `oce` Helm release and its upgrade lifecycle |
| Stage drift correction (`selfHeal`)       | The `openshell-gateway`, `openshell-dashboard` and Envoy Helm releases |
| | System Secrets, SQL bootstrap, `grant-tenant-access`, image builds, `occ` operations |

Argo CD renders Helm charts with `helm template`. It cannot run the OpenShell
gateway post-renderer, its `lookup()` guards are inert, and the OCE helpers
mutate the `oce` release (`attemptId`, `upgradeAttemptId`) while running
migrations and fleet redeploys. Owning that release from GitOps would
reimplement the lifecycle that AGENTS.md assigns to OCE, so it stays manual.

## Layout

```text
gitops/
├── bootstrap/              One-time cluster bootstrap (operator, RBAC, AppProject, health patch)
├── clusters/
│   ├── stage/              Root Application and one Application per platform stage
│   └── prod/               Placeholder; not deployable yet
```

The stage Kustomize profile is `manifests/openshift/overlays/stage`; the
original `overlays/test` profile is unchanged and still drives the runbook.

## Bootstrap a cluster

Review the operator channel first, then run the wrapper from the repository
root against the intended cluster:

```bash
oc -n openshift-marketplace get packagemanifest openshift-gitops-operator \
  -o jsonpath='{.status.channels[*].name}{"\n"}'

scripts/bootstrap-argocd --env stage --dry-run

scripts/bootstrap-argocd --env stage
```

The script applies the Subscription, polls for the default Argo CD instance and
waits for it to become Available, applies the RBAC and AppProject, patches the
resource health checks into the `ArgoCD` custom resource, and registers the
root Application. Rerunning it re-asserts the committed values, including
the whole `spec.resourceHealthChecks` list: fold any cluster-side health
customizations into `gitops/bootstrap/argocd-resource-health.patch.yaml` first.
It does not touch Secrets, the database, or OCE.

On a cluster already deployed with the runbooks, Argo starts syncing as soon as
the Applications exist, so the committed manifests do not provide an
`argocd app diff` gate before the first sync. Compare the `overlays/stage`
render with the live objects first, or redeploy the stage cluster from this
layout. Do not bootstrap an adopted cluster until that comparison is clean.

## Sync order and policy

| Application     | Wave | Stage        | Auto-sync | Gated by                    |
| --------------- | ---- | ------------ | --------- | --------------------------- |
| `oce-operators` | 0    | `operators`  | yes       | Subscription health check   |
| `oce-database`  | 1    | `database`   | yes       | PostgresCluster health check |
| `oce-admission` | 1    | `admission`  | yes       | namespaces and SCC applied  |
| `oce-openshell` | 2    | `openshell`  | yes       | wave 1 Healthy              |
| `oce-routing`   | 3    | `routing`    | yes       | wave 2 Healthy              |

Prune is off everywhere: removing a resource from Git never deletes it from the
cluster until an operator does so by hand. The root gates each wave on the child
Applications' health (`argoproj.io/Application`), the Subscription check holds
wave 0 until every CSV is at its latest known version, and the retry backoff
remains the backstop when a healthy Subscription still has not registered its
CRDs.

## Revision and freeze

The Applications track `main`. AGENTS.md's freeze rule governs the OCE source
commit and image digests recorded for each release; those stay in the private
inputs and the upgrade record, not in this tree. Prod will track its own branch
or tag once the prod cluster exists so a stage merge cannot reach it.

## Stage data

`manifests/openshift/overlays/stage` commits only two deviations: an
`oce-ocp-setup/environment: stage` label and a placeholder 10Gi PostgreSQL
volume. The label is metadata only; every NetworkPolicy selector stays identical
to the base, because the Pods those selectors target are created outside these
overlays.

These values are cluster-discovered and stay in `OCC_INPUT_DIRECTORY`:
the Console Route hostname, the Kubernetes API and PostgreSQL egress address
inputs, the router CIDRs, the private gateway host, image digests and every
Secret. See [the stage profile](../manifests/openshift/overlays/stage/README.md).

## Deferred: charts from the downstream fork

Helm charts are moving to `red-hat-data-services/openclaw-enterprise`. Consuming
them needs a merged, pinnable revision; the fork has no tags today. The follow-up
uses Argo CD multi-source: the chart path from the fork plus `valueFiles` from
this repository (`$values/...`). The dashboard chart is the first candidate. Do
not add the `oce` chart; see the ownership table.

## Version note

OpenShift GitOps 1.19 ships Argo CD 3.1 and 1.20 ships 3.3. Multi-source
Applications need 2.6 or later, so both work for the follow-up.

## Verification

Everything here renders offline:

```bash
oc kustomize gitops/bootstrap
oc kustomize gitops/clusters/stage/apps
oc kustomize manifests/openshift/overlays/stage
python3 -m unittest discover -s tests
```

There is no live Argo CD verification in this change: no disposable cluster was
available. The first real bootstrap should watch the waves and record the
installed CSV and Argo CD version.
