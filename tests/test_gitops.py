"""Offline checks for the GitOps tree and the Argo CD bootstrap script."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover - exercised where PyYAML is absent
    yaml = None

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "scripts" / "bootstrap-argocd"
KUBECTL = shutil.which("kubectl") or shutil.which("oc")
HAVE_YAML = yaml is not None

POSTGRES_CLUSTER = "postgres-operator.crunchydata.com/PostgresCluster"
SUBSCRIPTION = "operators.coreos.com/Subscription"
APPLICATION = "argoproj.io/Application"

STAGES = ("operators", "database", "admission", "openshell", "routing")
APPLICATIONS = {
    "oce-operators": ("0", "operators", "openclaw-postgres", 10),
    "oce-database": ("1", "database", "openclaw-postgres", 50),
    "oce-admission": ("1", "admission", "openclaw-system", 10),
    "oce-openshell": ("2", "openshell", "openshell-system", 10),
    "oce-routing": ("3", "routing", "envoy-gateway-system", 10),
}
REPO_URL = "https://github.com/rh-oce-team/oce-ocp-setup.git"
RETRY_BACKOFF = {"duration": "15s", "factor": 2, "maxDuration": "3m"}


def first_which(*names):
    for name in names:
        path = shutil.which(name)
        if path is not None:
            return path
    return None


LUA = first_which("lua", "lua5.4", "lua5.3", "luajit")
LUAC = first_which("luac", "luac5.4", "luac5.3")


def render(path):
    """Render a Kustomize path to a list of documents."""
    result = subprocess.run(
        [KUBECTL, "kustomize", str(REPOSITORY / path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def object_key(document):
    metadata = document.get("metadata", {})
    return (
        document.get("kind"),
        metadata.get("namespace"),
        metadata.get("name"),
    )


def health_checks():
    patch = yaml.safe_load(
        (REPOSITORY / "gitops/bootstrap/argocd-resource-health.patch.yaml").read_text()
    )
    return patch["spec"]["resourceHealthChecks"]


def health_lua(identity):
    """Return an embedded health script from the ArgoCD health-check patch."""
    return next(
        entry["check"]
        for entry in health_checks()
        if f"{entry['group']}/{entry['kind']}" == identity
    )


class BootstrapScriptTests(unittest.TestCase):
    """Exercise scripts/bootstrap-argocd with a fake oc on PATH."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        binary = self.root / "bin"
        binary.mkdir()
        self.log = self.root / "oc.log"
        fake_oc = binary / "oc"
        fake_oc.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "with open(os.environ['FAKE_OC_LOG'], 'a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        )
        fake_oc.chmod(0o755)
        self.env = {
            **os.environ,
            "FAKE_OC_LOG": str(self.log),
            "PATH": f"{binary}:{os.environ['PATH']}",
        }

    def run_script(self, *arguments, env=None):
        return subprocess.run(
            ["bash", str(SCRIPT), *arguments],
            cwd=REPOSITORY,
            env=env or self.env,
            capture_output=True,
            text=True,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_unknown_environment_fails(self):
        result = self.run_script("--env", "staging")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown environment", result.stderr)
        self.assertFalse(self.log.exists())

    def test_prod_is_refused(self):
        result = self.run_script("--env", "prod")
        self.assertEqual(result.returncode, 2)
        self.assertIn("placeholder", result.stderr)

    def test_environment_is_required(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 2)
        self.assertIn("--env", result.stderr)

    def test_missing_oc_fails(self):
        empty = self.root / "empty"
        empty.mkdir()
        result = subprocess.run(
            [shutil.which("bash") or "/bin/bash", str(SCRIPT), "--env", "stage"],
            cwd=REPOSITORY,
            env={**self.env, "PATH": str(empty)},
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("oc is required", result.stderr)

    def test_env_equals_form_is_accepted(self):
        result = self.run_script("--env=stage", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gitops/clusters/stage/root.yaml", result.stdout)

    def test_env_without_a_value_fails(self):
        result = self.run_script("--env")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--env needs a value", result.stderr)
        self.assertFalse(self.log.exists())

    def test_unknown_argument_fails(self):
        result = self.run_script("--bogus")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown argument: --bogus", result.stderr)
        self.assertFalse(self.log.exists())

    def test_help_prints_usage_and_exits_zero(self):
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                result = self.run_script(flag)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Usage: scripts/bootstrap-argocd", result.stdout)
                self.assertFalse(self.log.exists())

    def test_missing_required_file_fails(self):
        script = self.root / "repo" / "scripts" / "bootstrap-argocd"
        script.parent.mkdir(parents=True)
        shutil.copy2(SCRIPT, script)
        repository = script.parent.parent
        required = (
            "gitops/bootstrap/gitops-operator.yaml",
            "gitops/bootstrap/kustomization.yaml",
            "gitops/bootstrap/argocd-resource-health.patch.yaml",
            "gitops/clusters/stage/root.yaml",
        )
        for relative in required:
            path = repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
        for relative in required:
            with self.subTest(missing=relative):
                (repository / relative).unlink()
                result = subprocess.run(
                    ["bash", str(script), "--env", "stage"],
                    cwd=repository,
                    env=self.env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("missing required file", result.stderr)
                self.assertIn(relative, result.stderr)
                self.assertFalse(self.log.exists())
                (repository / relative).write_text("")

    def test_happy_path_order(self):
        result = self.run_script("--env", "stage")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()

        def first(predicate):
            return next(index for index, call in enumerate(calls) if predicate(call))

        subscription = first(
            lambda call: call[:2] == ["apply", "-f"]
            and call[-1].endswith("gitops/bootstrap/gitops-operator.yaml")
        )
        wait = first(
            lambda call: call[:2] == ["-n", "openshift-gitops"] and call[2] == "wait"
        )
        resource_appears = first(
            lambda call: call[:3] == ["-n", "openshift-gitops", "get"]
            and "argocd" in call
        )
        bootstrap = first(
            lambda call: call[:2] == ["apply", "-k"]
            and call[-1].endswith("gitops/bootstrap")
        )
        patch = first(lambda call: "patch" in call and "argocd" in call)
        root = first(
            lambda call: call[:2] == ["apply", "-f"]
            and call[-1].endswith("gitops/clusters/stage/root.yaml")
        )

        self.assertLess(subscription, wait)
        self.assertLess(resource_appears, wait)
        self.assertLess(wait, bootstrap)
        self.assertLess(bootstrap, patch)
        self.assertLess(patch, root)
        self.assertIn("--type", calls[patch])
        self.assertIn("merge", calls[patch])
        self.assertIn("--patch-file", calls[patch])
        for call in calls:
            self.assertFalse(
                call[:2] == ["apply", "-f"]
                and call[-1].endswith("argocd-resource-health.patch.yaml"),
                "a partial ArgoCD resource must be patched, never applied",
            )

    def test_dry_run_changes_nothing(self):
        result = self.run_script("--env", "stage", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls(),
            [["kustomize", str(REPOSITORY / "gitops/bootstrap")]],
        )
        self.assertIn("Would run: oc apply -f", result.stdout)
        self.assertIn("gitops/clusters/stage/root.yaml", result.stdout)
        self.assertNotIn("Argo now creates", result.stdout)


@unittest.skipUnless(HAVE_YAML and KUBECTL, "PyYAML and kubectl or oc are required")
class GitOpsTreeTests(unittest.TestCase):
    def test_applications(self):
        documents = render("gitops/clusters/stage/apps")
        applications = {
            document["metadata"]["name"]: document
            for document in documents
            if document.get("kind") == "Application"
        }
        self.assertEqual(set(applications), set(APPLICATIONS))
        for name, (wave, stage, namespace, retry_limit) in APPLICATIONS.items():
            application = applications[name]
            self.assertEqual(
                application["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"],
                wave,
            )
            self.assertEqual(application["spec"]["project"], "oce-stages")
            self.assertEqual(
                application["spec"]["source"]["path"],
                f"manifests/openshift/overlays/stage/{stage}",
            )
            self.assertEqual(application["spec"]["source"]["targetRevision"], "main")
            self.assertEqual(application["spec"]["destination"]["namespace"], namespace)
            self.assertFalse(application["spec"]["syncPolicy"]["automated"]["prune"])
            self.assertTrue(application["spec"]["syncPolicy"]["automated"]["selfHeal"])
            self.assertEqual(
                application["spec"]["syncPolicy"]["retry"]["limit"], retry_limit
            )

    def test_applications_source_repo_matches_app_project(self):
        project = yaml.safe_load(
            (REPOSITORY / "gitops/bootstrap/app-project.yaml").read_text()
        )
        allowed = project["spec"]["sourceRepos"]
        for document in render("gitops/clusters/stage/apps"):
            repo_url = document["spec"]["source"]["repoURL"]
            self.assertEqual(repo_url, REPO_URL, document["metadata"]["name"])
            self.assertIn(repo_url, allowed, document["metadata"]["name"])
        root = yaml.safe_load(
            (REPOSITORY / "gitops/clusters/stage/root.yaml").read_text()
        )
        self.assertEqual(root["spec"]["source"]["repoURL"], REPO_URL)

    def test_applications_retry_backoff(self):
        for document in render("gitops/clusters/stage/apps"):
            self.assertEqual(
                document["spec"]["syncPolicy"]["retry"]["backoff"],
                RETRY_BACKOFF,
                document["metadata"]["name"],
            )

    def test_applications_kustomization_sets_namespace_and_labels(self):
        kustomization = yaml.safe_load(
            (REPOSITORY / "gitops/clusters/stage/apps/kustomization.yaml").read_text()
        )
        self.assertEqual(kustomization["namespace"], "openshift-gitops")
        transformer = next(
            entry for entry in kustomization["labels"] if "pairs" in entry
        )
        self.assertFalse(transformer["includeSelectors"])
        self.assertEqual(
            transformer["pairs"],
            {
                "app.kubernetes.io/part-of": "oce-gitops",
                "oce-ocp-setup/cluster": "stage",
            },
        )
        for document in render("gitops/clusters/stage/apps"):
            self.assertEqual(document["metadata"]["namespace"], "openshift-gitops")
            labels = document["metadata"]["labels"]
            self.assertEqual(labels["app.kubernetes.io/part-of"], "oce-gitops")
            self.assertEqual(labels["oce-ocp-setup/cluster"], "stage")

    def test_root_application_points_at_the_stage_tree(self):
        root = yaml.safe_load(
            (REPOSITORY / "gitops/clusters/stage/root.yaml").read_text()
        )
        self.assertEqual(root["spec"]["source"]["path"], "gitops/clusters/stage/apps")
        self.assertEqual(root["metadata"]["namespace"], "openshift-gitops")
        self.assertEqual(
            root["metadata"]["labels"],
            {
                "app.kubernetes.io/part-of": "oce-gitops",
                "oce-ocp-setup/cluster": "stage",
            },
        )

    def test_root_application_uses_default_project_and_automated_sync(self):
        root = yaml.safe_load(
            (REPOSITORY / "gitops/clusters/stage/root.yaml").read_text()
        )
        self.assertEqual(root["spec"]["project"], "default")
        automated = root["spec"]["syncPolicy"]["automated"]
        self.assertFalse(automated["prune"])
        self.assertTrue(automated["selfHeal"])
        retry = root["spec"]["syncPolicy"]["retry"]
        self.assertEqual(retry["limit"], 5)
        self.assertEqual(retry["backoff"], RETRY_BACKOFF)

    def test_rbac_allows_admission_to_create_scc(self):
        documents = list(
            yaml.safe_load_all((REPOSITORY / "gitops/bootstrap/rbac.yaml").read_text())
        )
        role = next(
            document for document in documents if document["kind"] == "ClusterRole"
        )
        rule = next(
            rule
            for rule in role["rules"]
            if "securitycontextconstraints" in rule["resources"]
        )
        for verb in ("create", "update", "patch"):
            self.assertIn(verb, rule["verbs"])

    def test_rbac_binds_stages_role_to_the_application_controller(self):
        documents = list(
            yaml.safe_load_all((REPOSITORY / "gitops/bootstrap/rbac.yaml").read_text())
        )
        role = next(
            document for document in documents if document["kind"] == "ClusterRole"
        )
        binding = next(
            document
            for document in documents
            if document["kind"] == "ClusterRoleBinding"
        )
        self.assertEqual(binding["metadata"]["name"], "oce-argocd-stages")
        self.assertEqual(
            binding["roleRef"],
            {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": "oce-argocd-stages",
            },
        )
        self.assertEqual(binding["roleRef"]["name"], role["metadata"]["name"])
        self.assertEqual(
            binding["subjects"],
            [
                {
                    "kind": "ServiceAccount",
                    "name": "openshift-gitops-argocd-application-controller",
                    "namespace": "openshift-gitops",
                }
            ],
        )

    def test_app_project_scope(self):
        project = yaml.safe_load(
            (REPOSITORY / "gitops/bootstrap/app-project.yaml").read_text()
        )
        self.assertEqual(
            project["spec"]["sourceRepos"],
            ["https://github.com/rh-oce-team/oce-ocp-setup.git"],
        )
        self.assertEqual(
            {
                destination["namespace"]
                for destination in project["spec"]["destinations"]
            },
            {
                "openclaw-postgres",
                "cert-manager-operator",
                "openclaw-system",
                "openshell-system",
                "envoy-gateway-system",
            },
        )
        self.assertEqual(
            {
                (entry["group"], entry["kind"])
                for entry in project["spec"]["clusterResourceWhitelist"]
            },
            {
                ("", "Namespace"),
                ("security.openshift.io", "SecurityContextConstraints"),
                ("node.k8s.io", "RuntimeClass"),
                ("rbac.authorization.k8s.io", "ClusterRole"),
                ("gateway.networking.k8s.io", "GatewayClass"),
            },
        )

    def test_health_patch_targets_the_wave_crs(self):
        identities = {
            f"{entry['group']}/{entry['kind']}" for entry in health_checks()
        }
        self.assertEqual(
            identities,
            {POSTGRES_CLUSTER, SUBSCRIPTION, APPLICATION},
        )

    def test_bootstrap_does_not_render_the_patch_file(self):
        documents = render("gitops/bootstrap")
        self.assertNotIn("ArgoCD", {document.get("kind") for document in documents})

    def test_stage_overlay_changes_only_the_environment_label_and_database_size(self):
        for stage in STAGES:
            base = {
                object_key(document): document
                for document in render(f"manifests/openshift/base/{stage}")
            }
            overlay = {
                object_key(document): document
                for document in render(f"manifests/openshift/overlays/stage/{stage}")
            }
            self.assertEqual(set(base), set(overlay), stage)
            for key, document in overlay.items():
                labels = document["metadata"].get("labels", {})
                labels.pop("oce-ocp-setup/environment", None)
                if not labels:
                    document["metadata"].pop("labels", None)
                if stage == "database":
                    document["spec"]["instances"][0]["dataVolumeClaimSpec"]["resources"][
                        "requests"
                    ]["storage"] = "5Gi"
                self.assertEqual(base[key], document, f"{stage}/{key}")

    def test_stage_database_overlay_renders_the_documented_10gi_volume(self):
        def postgres_cluster(path):
            return next(
                document
                for document in render(path)
                if document.get("kind") == "PostgresCluster"
            )

        def storage(document):
            return document["spec"]["instances"][0]["dataVolumeClaimSpec"][
                "resources"
            ]["requests"]["storage"]

        base = postgres_cluster("manifests/openshift/base/database")
        overlay = postgres_cluster("manifests/openshift/overlays/stage/database")
        self.assertEqual(storage(base), "5Gi")
        self.assertEqual(storage(overlay), "10Gi")

    def test_stage_overlay_keeps_network_policy_selectors(self):
        base = {
            object_key(document): document
            for document in render("manifests/openshift/base/openshell")
            if document.get("kind") == "NetworkPolicy"
        }
        overlay = {
            object_key(document): document
            for document in render("manifests/openshift/overlays/stage/openshell")
            if document.get("kind") == "NetworkPolicy"
        }
        self.assertEqual(set(base), set(overlay))
        for key, document in base.items():
            self.assertEqual(
                document["spec"]["podSelector"],
                overlay[key]["spec"]["podSelector"],
            )
            for field in ("ingress", "egress"):
                if field in document["spec"]:
                    self.assertEqual(
                        document["spec"][field], overlay[key]["spec"][field]
                    )


@unittest.skipUnless(HAVE_YAML, "PyYAML is required")
class HealthLuaTests(unittest.TestCase):
    """Compile and exercise the health checks Argo CD runs for the waves."""

    def write_health_script(self, kind):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "health.lua"
        path.write_text(health_lua(kind))
        return path

    def run_driver(self, kind, driver):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "driver.lua"
        path.write_text(driver)
        return subprocess.run(
            [LUA, str(path)],
            env={**os.environ, "HEALTH_LUA": str(self.write_health_script(kind))},
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(LUA or LUAC, "lua or luac is required")
    def test_health_lua_parses(self):
        for kind in (POSTGRES_CLUSTER, SUBSCRIPTION, APPLICATION):
            with self.subTest(kind=kind):
                path = self.write_health_script(kind)
                if LUAC:
                    command = [LUAC, "-p", str(path)]
                    env = None
                else:
                    command = [
                        LUA,
                        "-e",
                        'assert(loadfile(os.getenv("HEALTH_LUA")))',
                    ]
                    env = {**os.environ, "HEALTH_LUA": str(path)}
                result = subprocess.run(
                    command, capture_output=True, text=True, env=env
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(LUA, "lua is required")
    def test_postgres_health_lua_reports_readiness(self):
        result = self.run_driver(
            POSTGRES_CLUSTER,
            """\
local path = assert(os.getenv("HEALTH_LUA"))
local function check(description, obj, expected)
  _G.obj = obj
  local hs = dofile(path)
  assert(hs and hs.status == expected,
    description .. ": expected " .. expected .. ", got " .. tostring(hs and hs.status))
end
check("ready",
  {status = {instances = {{replicas = 1, readyReplicas = 1}}}}, "Healthy")
check("not ready",
  {status = {instances = {{replicas = 3, readyReplicas = 2}}}}, "Progressing")
check("one instance pending",
  {status = {instances = {{replicas = 1, readyReplicas = 1},
    {replicas = 2, readyReplicas = 1}}}}, "Progressing")
check("counts unset", {status = {instances = {{}}}}, "Progressing")
check("missing status", {}, "Progressing")
check("missing instances", {status = {}}, "Progressing")
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(LUA, "lua is required")
    def test_subscription_health_lua_reports_readiness(self):
        result = self.run_driver(
            SUBSCRIPTION,
            """\
local path = assert(os.getenv("HEALTH_LUA"))
local function check(description, obj, expected)
  _G.obj = obj
  local hs = dofile(path)
  assert(hs and hs.status == expected,
    description .. ": expected " .. expected .. ", got " .. tostring(hs and hs.status))
end
check("at latest known", {status = {state = "AtLatestKnown"}}, "Healthy")
check("upgrade pending", {status = {state = "UpgradePending"}}, "Progressing")
check("upgrade failed", {status = {state = "UpgradeFailed"}}, "Degraded")
check("install plan failed condition",
  {status = {state = "AtLatestKnown",
    conditions = {{type = "InstallPlanFailed", status = "True"}}}}, "Degraded")
check("resolution failed condition",
  {status = {state = "AtLatestKnown",
    conditions = {{type = "ResolutionFailed", status = "True"}}}}, "Degraded")
check("catalog unhealthy condition",
  {status = {state = "AtLatestKnown",
    conditions = {{type = "CatalogSourcesUnhealthy", status = "True"}}}}, "Degraded")
check("missing install plan",
  {status = {state = "AtLatestKnown",
    conditions = {{type = "InstallPlanMissing", status = "True",
      reason = "ReferencedInstallPlanNotFound"}}}}, "Healthy")
check("install plan pending condition",
  {status = {state = "AtLatestKnown",
    conditions = {{type = "InstallPlanPending", status = "True"}}}}, "Progressing")
check("pending then failed",
  {status = {state = "AtLatestKnown",
    conditions = {{type = "InstallPlanPending", status = "True"},
      {type = "ResolutionFailed", status = "True"}}}}, "Degraded")
check("failed then pending",
  {status = {state = "AtLatestKnown",
    conditions = {{type = "ResolutionFailed", status = "True"},
      {type = "InstallPlanPending", status = "True"}}}}, "Degraded")
check("failed upgrade with pending plan",
  {status = {state = "UpgradeFailed",
    conditions = {{type = "InstallPlanPending", status = "True"}}}}, "Degraded")
check("false condition ignored",
  {status = {state = "AtLatestKnown",
    conditions = {{type = "InstallPlanFailed", status = "False"}}}}, "Healthy")
check("missing status", {}, "Progressing")
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr)


    @unittest.skipUnless(LUA, "lua is required")
    def test_application_health_lua_propagates_child_health(self):
        result = self.run_driver(
            APPLICATION,
            """\
local path = assert(os.getenv("HEALTH_LUA"))
local function check(description, obj, expected)
  _G.obj = obj
  local hs = dofile(path)
  assert(hs and hs.status == expected,
    description .. ": expected " .. expected .. ", got " .. tostring(hs and hs.status))
end
check("healthy", {status = {health = {status = "Healthy"}}}, "Healthy")
check("degraded", {status = {health = {status = "Degraded"}}}, "Degraded")
check("progressing", {status = {health = {status = "Progressing"}}}, "Progressing")
check("missing status", {}, "Progressing")
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
