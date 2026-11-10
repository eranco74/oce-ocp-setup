# Stage cluster profile

The stage profile for the five Kustomize stages Argo CD reconciles from
`gitops/clusters/stage`. It is a set of overlays on `../../base`, not a full
deployment procedure. Read [the GitOps guide](../../../../docs/gitops.md) for the
ownership boundary and [the Kustomize guide](../../../../docs/kustomize.md) for the
stage inventory.

The committed values are placeholders for a public repository. Replace them
before a real redeploy; the `oce-ocp-setup/environment: stage` label and the
10Gi PostgreSQL volume are the only deviations from the base today.

| Stage         | Argo Application  | Wave | Namespace             | Deviation                     |
| ------------- | ----------------- | ---- | --------------------- | ----------------------------- |
| `operators`   | `oce-operators`   | 0    | `openclaw-postgres`   | environment label             |
| `database`    | `oce-database`    | 1    | `openclaw-postgres`   | environment label, 10Gi       |
| `admission`   | `oce-admission`   | 1    | `openclaw-system`     | environment label             |
| `openshell`   | `oce-openshell`   | 2    | `openshell-system`    | environment label             |
| `routing`     | `oce-routing`     | 3    | `envoy-gateway-system`| environment label             |

`control-plane`, `control-ui`, and `control-ui-access` stay runbook steps. Their
hostnames, credentials, and OAuth client inputs are discovered per cluster and
keep flowing through `scripts/prepare-inputs` and `scripts/prepare-kustomize`
against `../test`.

## Values that are not committed

These change per cluster and must be supplied from the private input directory
at redeploy time. The base carries documentation-only placeholders
(`192.0.2.20/32`, `console.oce.example.invalid`) that will not work on a real
cluster.

- The Console Route hostname and the ingress domain.
- The Kubernetes API endpoint CIDRs and port used by the OCC API egress policy.
- The PostgreSQL primary Pod address used by the database egress policy.
- The router Pod CIDRs and the private gateway hostname.
- Image digests for the controller, runtime, and OpenShell workspace images.
- Every Secret, including the Installation startup Secret.

## Why `labels` and not `commonLabels`

The `labels` transformer with `includeSelectors: false` and
`includeTemplates: false` adds the environment label to object metadata only.
`commonLabels` would also inject it into every NetworkPolicy `podSelector` and
`namespaceSelector`. The Pods those selectors target are created outside these
overlays (the `oce` Helm release, OCE-managed workspace Pods, and the
OpenShell gateway release), so the selectors would match nothing and the
policies would deny the traffic they exist to allow.
