## Cluster access (read-only)

Use the `kuberead` helper next to this file for questions about the OCE
OpenShift cluster. It is read-only and limited to the OCE namespaces.

- `kuberead get pods -n <namespace>` lists Pods; other resources include
  deployments, services, routes, events and jobs.
- `kuberead logs <pod> -n <namespace>` reads a Pod log.
- `kuberead raw <api-path>` reads any other API path with a GET.

The credential works only in the OCE namespaces (`oce-*`, `openclaw-*`,
`openshell-*`, `agent-sandbox-system`, `envoy-gateway-system`) and cannot
read Secrets or ConfigMaps or change anything. Say so when asked for
something outside that scope instead of trying workarounds.
