# OpenShift test deployment

Start with the [deployment guide](../../docs/README.md).
Use dedicated Codex through OpenShell and verify the target
cluster's storage, runtime, Operators, and networking.

The supporting Kubernetes resources use checked-in Kustomize files:

```text
base/                         Resource manifests grouped by deployment stage
base/kustomization.yaml        All base resources for inspection
  operators/, database/, admission/, openshell/, control-plane/, routing/
  control-ui/, control-ui-access/
overlays/test/                 Test profile, with a Kustomization for each stage
../../scripts/prepare-kustomize  Prepare private overlays with deployment overrides
../../scripts/verify-images     Verify built image architectures and save digest references
../../scripts/resolve-images    Pin published Quay images to amd64 digests instead of building
../../scripts/grant-tenant-access  Apply exact worker/API grants
```

Render the complete example to inspect its resources:

```bash
oc kustomize manifests/openshift/overlays/test
```

Deploy individual stages in the [Kustomize guide](../../docs/kustomize.md)
order, with readiness checks between dependencies. Example hostnames, network
addresses, and `${VARIABLE}` values need private deployment overrides before
application. Credentials and generated overlays stay outside this checkout.

Helm values, Installation fragments, and image-build templates remain alongside
these directories. Helm still manages OCC, OpenShell, and Envoy; the owning
guides cover builds, database initialization, credentials, and readiness waits.

The optional `openshell-git/` resources are outside the base stages. Its
Kustomization prepares OpenShift service-CA resources; the Helm fragments and
supervisor egress rule require private inputs and explicit rollout steps from
the [protected Git guide](../../docs/openshell-github-app.md).
