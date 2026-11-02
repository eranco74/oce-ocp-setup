# Add the OpenShell dashboard to the OpenShift dev cluster

Run from the `oce-ocp-setup` repository root after OCC, OpenShell 0.1.6, and the
single-instance Crunchy PostgreSQL database are working. This recipe installs
dashboard 0.1.0 in `openshell-system` and Keycloak in `openshell-auth`.
It creates one `dashboard-admin` login. Keycloak is used only by this dashboard;
OCC login and the Agents' OpenShift OAuth login stay separate.

Use a cluster-admin login. Preserve existing database credentials, PVCs, and
OpenShell Helm values. Keycloak gets a separate database and role on the dev
PostgreSQL instance; it inherits that instance's lack of backups and HA.
Commands below are for a fresh dashboard installation. Realm imports create
new realms only; reapplying them does not update existing users or clients.

## 1. Select inputs and install the operator

```bash
umask 077

# REQUIRED: private kubeconfig for the intended cluster.
export KUBECONFIG='<private cluster kubeconfig>'

# REQUIRED: OCC setup directory containing occ-database-ca.pem and OpenShell charts.
export OCC_SETUP_DIRECTORY='<OCC setup directory>'

export DASHBOARD_INPUT_DIRECTORY="$HOME/.local/state/openclaw-enterprise/openshell-dashboard"

install -d -m 700 "$DASHBOARD_INPUT_DIRECTORY"

oc whoami

oc whoami --show-server

export CLUSTER_DOMAIN="$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')"

export KEYCLOAK_HOST="openshell-auth.$CLUSTER_DOMAIN"

export DASHBOARD_HOST="openshell-dashboard.$CLUSTER_DOMAIN"

oc apply -f manifests/dashboard/keycloak-operator.yaml
```

Wait for the Subscription's InstallPlan reference. Approve only the pinned CSV:

```bash
export KEYCLOAK_PLAN="$(oc -n openshell-auth get subscription rhbk-operator \
  -o jsonpath='{.status.installPlanRef.name}')"

test -n "$KEYCLOAK_PLAN"

oc -n openshell-auth get installplan "$KEYCLOAK_PLAN" -o json |
  jq -e '.spec.clusterServiceVersionNames == ["rhbk-operator.v26.6.7-opr.1"]'

oc -n openshell-auth patch installplan "$KEYCLOAK_PLAN" \
  --type=merge -p '{"spec":{"approved":true}}'

oc -n openshell-auth get csv rhbk-operator.v26.6.7-opr.1
```

Wait until that CSV exists and reports `Succeeded`. Future operator upgrades
also require explicit InstallPlan approval.

## 2. Prepare Keycloak's database and protected credentials

```bash
python3 - "$DASHBOARD_INPUT_DIRECTORY" <<'PY'
from pathlib import Path
import secrets, sys

root = Path(sys.argv[1])
for name in ('database-password', 'client-secret', 'cookie-secret', 'user-password'):
    with (root / name).open('x') as output:
        output.write(secrets.token_hex(24))
(root / 'database-username').write_text('openshell_keycloak')
PY

export POSTGRES_POD="$(oc -n openclaw-postgres get pods \
  -l postgres-operator.crunchydata.com/cluster=oce-postgres,postgres-operator.crunchydata.com/role=master \
  -o jsonpath='{.items[0].metadata.name}')"

set -o pipefail

(
  printf "\\set keycloak_password '%s'\n" "$(cat "$DASHBOARD_INPUT_DIRECTORY/database-password")"
  cat manifests/dashboard/database.sql
) | oc -n openclaw-postgres exec -i "$POSTGRES_POD" -c database -- \
  psql -U postgres -d postgres -X -v ON_ERROR_STOP=1 -f -

oc -n openshell-auth create secret generic keycloak-database \
  --from-file=username="$DASHBOARD_INPUT_DIRECTORY/database-username" \
  --from-file=password="$DASHBOARD_INPUT_DIRECTORY/database-password"

oc -n openshell-auth create secret generic keycloak-database-ca \
  --from-file=ca.pem="$OCC_SETUP_DIRECTORY/occ-database-ca.pem"

oc -n openshell-auth create secret generic dashboard-realm-bootstrap \
  --from-file=client-secret="$DASHBOARD_INPUT_DIRECTORY/client-secret" \
  --from-file=user-password="$DASHBOARD_INPUT_DIRECTORY/user-password"
```

The SQL creates only `openshell_keycloak`; it does not migrate or change OCC's
database. Password files refuse overwrites. Stop on any error instead of
generating replacement passwords for an already-created database.

## 3. Start Keycloak and import the realm

Render only hostname variables; the operator resolves credential placeholders
from Secrets. Keycloak HTTP ingress is restricted to the default router Pods,
which terminate HTTPS using the cluster's certificate.

```bash
for file in keycloak.yaml realm-import.yaml dashboard-values.yaml gateway-oidc-values.yaml; do
  envsubst '${KEYCLOAK_HOST} ${DASHBOARD_HOST}' \
    < "manifests/dashboard/$file" \
    > "$DASHBOARD_INPUT_DIRECTORY/$file"
done

oc apply --dry-run=server -f "$DASHBOARD_INPUT_DIRECTORY/keycloak.yaml"

oc apply -f "$DASHBOARD_INPUT_DIRECTORY/keycloak.yaml"

oc -n openshell-auth wait --for=condition=Ready \
  keycloak/openshell-keycloak --timeout=5m

oc apply -f "$DASHBOARD_INPUT_DIRECTORY/realm-import.yaml"

oc -n openshell-auth wait --for=condition=Done \
  keycloakrealmimport/openshell-dashboard --timeout=5m

curl --fail --silent --show-error \
  "https://$KEYCLOAK_HOST/realms/openshell/.well-known/openid-configuration" >/dev/null
```

Use the Keycloak administrator console for later account changes. Its separate
initial administrator credentials are stored in the operator-generated Secret
`openshell-keycloak-initial-admin`; retrieve them through a protected procedure.
The dashboard user's credentials are not Keycloak administration credentials.

## 4. Add token validation to OpenShell

This preserves OCC's existing development connection mode. Do not disable that
mode or change OpenShell TLS as part of this recipe: OCC's Backend would need a
separate authentication change. Adding OIDC does not secure every existing
private-network client. The gateway remains private and retains its ingress
restriction. Its rolling restart briefly interrupts gateway connections.
For an existing deployment's coordinated transport change, follow the
[mTLS cutover procedure](openshell-mtls.md); it retains this OIDC configuration
and adds public CA trust to the dashboard without an administrator client key.

```bash
helm get values openshell-gateway -n openshell-system -o yaml \
  > "$DASHBOARD_INPUT_DIRECTORY/gateway-values-before.yaml"

helm upgrade openshell-gateway \
  "$OCC_SETUP_DIRECTORY/openshell-charts/helm-chart-0.1.6.tgz" \
  --namespace openshell-system \
  -f "$DASHBOARD_INPUT_DIRECTORY/gateway-values-before.yaml" \
  -f "$DASHBOARD_INPUT_DIRECTORY/gateway-oidc-values.yaml" \
  --wait --timeout 7m
```

## 5. Install the dashboard

The published chart and images are pinned. No local dashboard build is needed.
Only the authentication proxy's port is exposed; the dashboard backend binds
Pod loopback. Use `-n openshell-system` even for dry runs: chart output omits
namespace fields, and the default namespace may be protected by managed admission.

```bash
helm pull oci://ghcr.io/gkrumbach07/openshell-dashboard/helm-chart/openshell-dashboard \
  --version 0.1.0 --destination "$DASHBOARD_INPUT_DIRECTORY"

printf '%s  %s\n' \
  ed169decf495ba9500c3d014a89bc52cb7c91add45219956b3d6982dd9fe6fa1 \
  "$DASHBOARD_INPUT_DIRECTORY/openshell-dashboard-0.1.0.tgz" | shasum -a 256 -c

oc -n openshell-system create secret generic openshell-dashboard-oidc \
  --from-file=client-secret="$DASHBOARD_INPUT_DIRECTORY/client-secret" \
  --from-file=cookie-secret="$DASHBOARD_INPUT_DIRECTORY/cookie-secret"

helm template openshell-dashboard "$DASHBOARD_INPUT_DIRECTORY/openshell-dashboard-0.1.0.tgz" \
  --namespace openshell-system -f "$DASHBOARD_INPUT_DIRECTORY/dashboard-values.yaml" \
  > "$DASHBOARD_INPUT_DIRECTORY/dashboard-rendered.yaml"

oc -n openshell-system apply --dry-run=server \
  -f "$DASHBOARD_INPUT_DIRECTORY/dashboard-rendered.yaml"

oc apply -f manifests/dashboard/dashboard-gateway-access.yaml

helm upgrade --install openshell-dashboard \
  "$DASHBOARD_INPUT_DIRECTORY/openshell-dashboard-0.1.0.tgz" \
  --namespace openshell-system -f "$DASHBOARD_INPUT_DIRECTORY/dashboard-values.yaml" \
  --wait --timeout 7m
```

## 6. Sign in and verify

```bash
oc -n openshell-system get deployments,pods,routes

printf 'Dashboard: https://%s\nKeycloak: https://%s\n' "$DASHBOARD_HOST" "$KEYCLOAK_HOST"

# Read locally; do not paste the password into chat or shared documentation.
cat "$DASHBOARD_INPUT_DIRECTORY/user-password"
```

Sign in as `dashboard-admin`. Confirm gateway compatibility and that the
dashboard lists the existing OCC workspace and Shelly's Sandbox. Complete a
model turn in Shelly's Control UI afterward to verify the preserved OCC path.
Use the dashboard to inspect OCC-managed resources; change their configuration
through OCC rather than creating drift through dashboard edits.

Retain rendered values and the chart checksum for updates. Share configuration
files only; exclude passwords, cookie secrets, client secrets, and kubeconfigs.
Include `gateway-oidc-values.yaml` in future OpenShell Helm upgrades so an
OCE-only values file does not remove the dashboard issuer configuration.
Keep the Keycloak database and realm between rollouts. Once the realm import is
Done, delete its CR to clean up its Job; this does not delete the realm:

```bash
oc -n openshell-auth delete keycloakrealmimport openshell-dashboard
```

For recovery from the OpenShell auth change, upgrade its release with
`gateway-values-before.yaml` alone. Do not delete the shared gateway, its claim,
the PostgreSQL instance, or operator CRDs to remove the dashboard.
