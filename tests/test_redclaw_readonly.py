"""Offline checks for the RedClaw read-only cluster access recipe."""

import base64
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

REPOSITORY = Path(__file__).resolve().parents[1]
MANIFEST = (
    REPOSITORY
    / "manifests"
    / "openshift"
    / "redclaw-readonly-role.json"
)
KUBEREAD = REPOSITORY / "manifests" / "agents" / "redclaw-readonly" / "kuberead"
AGENTS_FRAGMENT = (
    REPOSITORY / "manifests" / "agents" / "redclaw-readonly"
    / "agents-md-fragment.md"
)
GRANT_SCRIPT = REPOSITORY / "scripts" / "grant-redclaw-readonly"
PREPARE_SCRIPT = REPOSITORY / "scripts" / "prepare-redclaw-readonly"
READ_VERBS = {"get", "list", "watch"}
FORBIDDEN_RESOURCES = {"configmaps", "secrets"}


def load_manifest():
    with MANIFEST.open() as stream:
        return json.load(stream)


class ClusterRoleManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest()

    def test_name_and_structure(self):
        self.assertEqual(self.manifest["kind"], "ClusterRole")
        self.assertEqual(
            self.manifest["apiVersion"], "rbac.authorization.k8s.io/v1"
        )
        self.assertEqual(self.manifest["metadata"]["name"], "oce-redclaw-readonly")
        self.assertIsInstance(self.manifest["rules"], list)
        self.assertTrue(self.manifest["rules"])

    def test_no_configmaps_secrets_or_write_verbs(self):
        for rule in self.manifest["rules"]:
            verbs = set(rule.get("verbs", []))
            self.assertLessEqual(verbs, READ_VERBS, rule)
            resources = set(rule.get("resources", []))
            self.assertFalse(resources & FORBIDDEN_RESOURCES, rule)

    def test_expected_resources_present(self):
        def covered(resource, api_group=None):
            return any(
                resource in rule.get("resources", [])
                and (api_group is None or api_group in rule.get("apiGroups", []))
                for rule in self.manifest["rules"]
            )

        self.assertTrue(covered("pods"))
        self.assertTrue(covered("pods/log"))
        self.assertTrue(covered("routes", "route.openshift.io"))
        self.assertTrue(covered("deployments", "apps"))


class KubereadTests(unittest.TestCase):
    CA_PEM = "test-ca-pem\n"
    SERVER = "https://172.30.0.1:443"
    TOKEN = "testtoken"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "curl.log"
        shim = self.bin / "curl"
        shim.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import sys\n"
            "args = sys.argv[1:]\n"
            "contents = None\n"
            "if '--cacert' in args:\n"
            "    with open(args[args.index('--cacert') + 1]) as source:\n"
            "        contents = source.read()\n"
            "with open(" + repr(str(self.log)) + ", 'a') as stream:\n"
            "    stream.write(json.dumps({'argv': args, 'cacert': contents}) + '\\n')\n"
        )
        shim.chmod(0o755)
        self.kubeconfig = self.root / "kubeconfig"
        self.write_kubeconfig(self.kubeconfig)

    def write_kubeconfig(self, path, token=TOKEN):
        ca_data = base64.b64encode(self.CA_PEM.encode()).decode()
        lines = [
            "apiVersion: v1",
            "kind: Config",
            "clusters:",
            "- cluster:",
            f"    server: {self.SERVER}",
            f"    certificate-authority-data: {ca_data}",
            "  name: redclaw-readonly",
            "contexts:",
            "- context:",
            "    cluster: redclaw-readonly",
            "    user: redclaw-readonly",
            "  name: redclaw-readonly",
            "current-context: redclaw-readonly",
            "users:",
            "- name: redclaw-readonly",
            "  user:",
        ]
        if token is not None:
            lines.append(f"    token: {token}")
        path.write_text("\n".join(lines) + "\n")

    def run_kuberead(self, *args, kubeconfig=None):
        environment = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "KUBEREAD_KUBECONFIG": str(kubeconfig or self.kubeconfig),
        }
        return subprocess.run(
            [str(KUBEREAD), *args],
            env=environment,
            capture_output=True,
            text=True,
        )

    def curl_calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def single_call(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.curl_calls()
        self.assertEqual(len(calls), 1, calls)
        return calls[0]

    def test_get_namespaced_resource_builds_url_and_auth(self):
        call = self.single_call(
            self.run_kuberead("get", "pods", "-n", "oce-test")
        )
        arguments = call["argv"]
        self.assertEqual(call["cacert"], self.CA_PEM)
        self.assertIn("--cacert", arguments)
        self.assertIn(f"Authorization: Bearer {self.TOKEN}", arguments)
        self.assertEqual(
            arguments[-1],
            f"{self.SERVER}/api/v1/namespaces/oce-test/pods",
        )

    def test_get_all_namespaces_uses_collection_path(self):
        call = self.single_call(self.run_kuberead("get", "deployments"))
        self.assertEqual(
            call["argv"][-1], f"{self.SERVER}/apis/apps/v1/deployments"
        )

    def test_logs_uses_pod_log_path(self):
        call = self.single_call(
            self.run_kuberead("logs", "pod-1", "-n", "oce-test")
        )
        self.assertEqual(
            call["argv"][-1],
            f"{self.SERVER}/api/v1/namespaces/oce-test/pods/pod-1/log",
        )

    def test_raw_passes_path_through(self):
        call = self.single_call(
            self.run_kuberead(
                "raw", "/apis/batch/v1/namespaces/oce-test/jobs"
            )
        )
        self.assertEqual(
            call["argv"][-1],
            f"{self.SERVER}/apis/batch/v1/namespaces/oce-test/jobs",
        )

    def test_write_verbs_are_refused_without_curl(self):
        for verb in (
            "create",
            "apply",
            "delete",
            "patch",
            "replace",
            "edit",
            "label",
            "annotate",
            "scale",
            "rollout",
            "exec",
        ):
            with self.subTest(verb=verb):
                result = self.run_kuberead(verb, "pods")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("read-only", result.stderr)
                self.assertEqual(self.curl_calls(), [])

    def test_unknown_resource_fails_with_supported_list(self):
        result = self.run_kuberead("get", "widgets", "-n", "oce-test")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("raw", result.stderr)
        self.assertEqual(self.curl_calls(), [])

    def test_missing_token_fails_loudly(self):
        kubeconfig = self.root / "kubeconfig-no-token"
        self.write_kubeconfig(kubeconfig, token=None)
        result = self.run_kuberead("get", "pods", kubeconfig=kubeconfig)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kubeconfig", result.stderr)
        self.assertEqual(self.curl_calls(), [])


class GrantScriptTests(unittest.TestCase):
    SA = "agent-c66e33c482ff"
    TENANT = "oce-tenant-test"
    OTHER_TENANT = "oce-other-tenant"
    FIXED_NAMESPACES = [
        "openclaw-system",
        "openclaw-postgres",
        "openshell-system",
        "openshell-auth",
        "agent-sandbox-system",
        "envoy-gateway-system",
    ]
    OC_SHIM = """#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
stdin = sys.stdin.read() if "-" in args else ""
if "--patch-file" not in args:
    with open(%(log)r, "a") as stream:
        stream.write(json.dumps({"argv": args, "stdin": stdin}) + "\\n")
with open(%(state)r) as stream:
    state = json.load(stream)

if args[:2] == ["get", "serviceaccount"]:
    print(json.dumps({
        "items": [
            {"metadata": {"name": %(sa)r, "namespace": namespace}}
            for namespace in state["serviceaccounts"]
        ]
    }))
elif args[:2] == ["get", "namespaces"]:
    print("\\n".join(state["tenant_namespaces"]))
elif args[:2] == ["auth", "can-i"]:
    namespace = args[args.index("-n") + 1]
    key = args[2] + " " + args[3] + " -n " + namespace
    answer = state["can_i"].get(key, "no")
    print(answer)
    sys.exit(1 if answer == "no" else 0)
elif args[:2] == ["create", "token"] or args[2:4] == ["create", "token"]:
    print("minted-test-token")
elif args[:2] == ["get", "configmap"] or args[2:4] == ["get", "configmap"]:
    print("test-ca-pem")
elif args[:2] == ["patch", "secret"] or args[2:4] == ["patch", "secret"]:
    patch = {}
    with open(args[args.index("--patch-file") + 1]) as stream:
        patch = json.load(stream)
    with open(%(log)r, "a") as stream:
        stream.write(
            json.dumps({"argv": args, "stdin": stdin, "patch": patch}) + "\\n"
        )
    sys.exit(0)
"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "oc.log"
        self.state = self.root / "oc-state.json"
        self.write_state()
        shim = self.bin / "oc"
        shim.write_text(
            self.OC_SHIM
            % {
                "log": str(self.log),
                "state": str(self.state),
                "sa": self.SA,
            }
        )
        shim.chmod(0o755)
        environment = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
        }
        environment.pop("AGENT_SA", None)
        self.env = environment

    def write_state(self, serviceaccounts=None, can_i=None):
        state = {
            "serviceaccounts": (
                [self.TENANT] if serviceaccounts is None else serviceaccounts
            ),
            "tenant_namespaces": [self.TENANT, self.OTHER_TENANT],
            "can_i": (
                {
                    "list pods -n openclaw-system": "yes",
                    f"get configmaps -n {self.TENANT}": "no",
                }
                if can_i is None
                else can_i
            ),
        }
        self.state.write_text(json.dumps(state))

    def run_script(self):
        return subprocess.run(
            [str(GRANT_SCRIPT)],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [
            json.loads(line) for line in self.log.read_text().splitlines()
        ]

    def payloads(self, kind):
        documents = []
        for call in self.calls():
            if not call["stdin"]:
                continue
            document = json.loads(call["stdin"])
            if document.get("kind") == kind:
                documents.append((call, document))
        return documents

    def test_applies_clusterrole_manifest(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = ["apply", "-f", str(MANIFEST)]
        matches = [call for call in self.calls() if call["argv"] == expected]
        self.assertEqual(len(matches), 1, self.calls())

    def test_mints_token_and_ca_into_secret(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        minted = [
            call
            for call in self.calls()
            if "create" in call["argv"]
            and call["argv"][call["argv"].index("create") + 1] == "token"
        ]
        self.assertEqual(len(minted), 1, self.calls())
        self.assertIn(
            "--duration=31536000s",
            minted[0]["argv"],
        )
        patches = [
            call
            for call in self.calls()
            if "patch" in call["argv"]
            and call["argv"][call["argv"].index("patch") + 1] == "secret"
        ]
        self.assertEqual(len(patches), 1, self.calls())
        self.assertEqual(
            patches[0]["argv"][patches[0]["argv"].index("-n") + 1], self.TENANT
        )
        data = patches[0]["patch"]["data"]
        self.assertEqual(
            base64.b64decode(data["token"]).decode(), "minted-test-token"
        )
        self.assertEqual(
            base64.b64decode(data["ca.crt"]).decode(), "test-ca-pem"
        )

    def test_creates_serviceaccount_and_token_secret_in_discovered_tenant(
        self,
    ):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        for kind, name in (
            ("ServiceAccount", "redclaw-readonly"),
            ("Secret", "redclaw-readonly-token"),
        ):
            with self.subTest(kind=kind):
                payloads = self.payloads(kind)
                self.assertEqual(len(payloads), 1, payloads)
                call, document = payloads[0]
                self.assertEqual(document["metadata"]["name"], name)
                self.assertEqual(
                    call["argv"][call["argv"].index("-n") + 1], self.TENANT
                )
                self.assertEqual(
                    document["metadata"]["namespace"], self.TENANT
                )
        _, secret = self.payloads("Secret")[0]
        self.assertEqual(
            secret["type"], "kubernetes.io/serviceaccount-token"
        )
        self.assertEqual(
            secret["metadata"]["annotations"][
                "kubernetes.io/service-account.name"
            ],
            "redclaw-readonly",
        )

    def test_binds_every_scope_namespace(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        bindings = self.payloads("RoleBinding")
        namespaces = []
        for call, document in bindings:
            self.assertEqual(
                document["metadata"]["name"], "oce-redclaw-readonly"
            )
            self.assertEqual(
                document["roleRef"],
                {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "ClusterRole",
                    "name": "oce-redclaw-readonly",
                },
            )
            self.assertEqual(
                document["subjects"],
                [
                    {
                        "kind": "ServiceAccount",
                        "name": "redclaw-readonly",
                        "namespace": self.TENANT,
                    }
                ],
            )
            namespace = call["argv"][call["argv"].index("-n") + 1]
            self.assertEqual(document["metadata"]["namespace"], namespace)
            namespaces.append(namespace)
        self.assertEqual(len(namespaces), len(set(namespaces)))
        expected = set(self.FIXED_NAMESPACES)
        expected.update({self.TENANT, self.OTHER_TENANT})
        self.assertEqual(set(namespaces), expected)

    def test_fails_when_agent_sa_matches_multiple_namespaces(self):
        self.write_state(
            serviceaccounts=[self.TENANT, self.OTHER_TENANT]
        )
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(self.SA, result.stderr)
        applied = [
            call for call in self.calls() if "apply" in call["argv"]
        ]
        self.assertEqual(applied, [])

    def test_verifies_denied_configmap_access(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        impersonation = (
            f"--as=system:serviceaccount:{self.TENANT}:redclaw-readonly"
        )
        checks = [
            call["argv"]
            for call in self.calls()
            if call["argv"][:2] == ["auth", "can-i"]
        ]
        self.assertEqual(
            checks,
            [
                [
                    "auth",
                    "can-i",
                    "list",
                    "pods",
                    "-n",
                    "openclaw-system",
                    impersonation,
                ],
                [
                    "auth",
                    "can-i",
                    "get",
                    "configmaps",
                    "-n",
                    self.TENANT,
                    impersonation,
                ],
            ],
        )
        self.log.unlink()
        self.write_state(can_i={"list pods -n openclaw-system": "no"})
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("list pods", result.stderr)


class PrepareScriptTests(unittest.TestCase):
    SA = "agent-c66e33c482ff"
    TENANT = "oce-tenant-test"
    TOKEN = "tok123"
    TOKEN_B64 = base64.b64encode(TOKEN.encode()).decode()
    CA_PEM = "ca-pem\n"
    CA_B64 = base64.b64encode(CA_PEM.encode()).decode()
    CLUSTER_IP = "172.30.0.1"
    EXISTING_ADDITION = {
        "id": "suggestion-demo",
        "binary": "/usr/bin/curl",
        "host": "api.github.com",
        "ports": [{"from": 443, "to": 443}],
    }
    OC_SHIM = """#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
with open(%(log)r, "a") as stream:
    stream.write(json.dumps({"argv": args}) + "\\n")

with open(%(state)r) as stream:
    state = json.load(stream)

if "whoami" in args:
    if state.get("whoami_fails"):
        sys.exit(1)
    print("shim-user")
elif "serviceaccount" in args and "get" in args:
    print(json.dumps({
        "items": [
            {"metadata": {"name": %(sa)r, "namespace": namespace}}
            for namespace in state["serviceaccounts"]
        ]
    }))
elif "secret" in args and "get" in args:
    key = "ca" if "crt" in " ".join(args) else "token"
    print(state[key], end="")
elif "svc" in args and "get" in args:
    print(state["cluster_ip"], end="")
else:
    sys.exit(1)
"""
    OCC_SHIM = """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
with open(%(log)r, "a") as stream:
    stream.write(json.dumps({
        "argv": args,
        "OCC_URL": os.environ.get("OCC_URL"),
        "OCC_SERVICE_KEY_FILE": os.environ.get("OCC_SERVICE_KEY_FILE"),
    }) + "\\n")

with open(%(state)r) as stream:
    state = json.load(stream)

if state.get("fail"):
    sys.exit(1)

if state.get("invalid_json"):
    print("not-json")
    sys.exit(0)

if args[:3] == ["agent", "policy", "get"]:
    print(json.dumps(state["document"]))
    sys.exit(0)

sys.exit(2)
"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.oc_log = self.root / "oc.log"
        self.oc_state = self.root / "oc-state.json"
        self.occ_log = self.root / "occ.log"
        self.occ_state = self.root / "occ-state.json"
        self.inputs = self.root / "inputs"
        self.inputs.mkdir(mode=0o700)
        self.output = self.inputs / "redclaw-readonly"
        self.write_oc_state()
        self.write_occ_state()
        oc = self.bin / "oc"
        oc.write_text(
            self.OC_SHIM
            % {
                "log": str(self.oc_log),
                "state": str(self.oc_state),
                "sa": self.SA,
            }
        )
        oc.chmod(0o755)
        self.occ = self.root / "occ"
        self.occ.write_text(
            self.OCC_SHIM
            % {"log": str(self.occ_log), "state": str(self.occ_state)}
        )
        self.occ.chmod(0o755)
        environment = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "OCC_BIN": str(self.occ),
            "OCC_INPUT_DIRECTORY": str(self.inputs),
            "OCC_URL": "https://occ.example.test",
            "OCC_SERVICE_KEY_FILE": str(self.root / "service-key.json"),
            "OCC_AGENT_ID": "agt_test",
            "OCC_NAMESPACE_ID": "ns_test",
        }
        environment.pop("AGENT_SA", None)
        self.env = environment

    def write_oc_state(self, token=None, ca=None, **overrides):
        state = {
            "serviceaccounts": [self.TENANT],
            "token": self.TOKEN_B64 if token is None else token,
            "ca": self.CA_B64 if ca is None else ca,
            "cluster_ip": self.CLUSTER_IP,
        }
        state.update(overrides)
        self.oc_state.write_text(json.dumps(state))

    def write_occ_state(self, fail=False, additions=None, invalid_json=False):
        if additions is None:
            additions = [self.EXISTING_ADDITION]
        document = {"policy": {"version": 7, "additions": additions}}
        self.occ_state.write_text(
            json.dumps(
                {
                    "fail": fail,
                    "invalid_json": invalid_json,
                    "document": document,
                }
            )
        )

    def run_script(self):
        return subprocess.run(
            [str(PREPARE_SCRIPT)],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def occ_calls(self):
        if not self.occ_log.exists():
            return []
        return [
            json.loads(line) for line in self.occ_log.read_text().splitlines()
        ]

    def test_writes_kubeconfig_with_pinned_format(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.output / "kubeconfig"
        self.assertTrue(path.is_file())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        text = path.read_text()
        lines = text.splitlines()
        server = f"    server: https://{self.CLUSTER_IP}:443"
        ca = f"    certificate-authority-data: {self.CA_B64}"
        token = f"    token: {self.TOKEN}"
        self.assertIn("apiVersion: v1", lines)
        self.assertIn("kind: Config", lines)
        self.assertIn(server, lines)
        self.assertIn(ca, lines)
        self.assertIn(token, lines)
        self.assertLess(lines.index(server), lines.index(ca))
        self.assertLess(lines.index("  user:"), lines.index(token))
        self.assertEqual(text.count("clusters:"), 1)
        self.assertEqual(text.count("contexts:"), 1)
        self.assertEqual(text.count("users:"), 1)
        self.assertEqual(text.count("- cluster:"), 1)
        self.assertEqual(text.count("- context:"), 1)
        self.assertEqual(text.count("- name: redclaw-readonly"), 1)
        self.assertEqual(text.count("  name: redclaw-readonly"), 2)
        self.assertIn("    cluster: redclaw-readonly", lines)
        self.assertIn("    user: redclaw-readonly", lines)
        self.assertIn("current-context: redclaw-readonly", lines)

    def test_copies_payloads(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        kuberead = self.output / "kuberead"
        self.assertTrue(kuberead.is_file())
        self.assertEqual(stat.S_IMODE(kuberead.stat().st_mode), 0o755)
        self.assertEqual(kuberead.read_bytes(), KUBEREAD.read_bytes())
        fragment = self.output / "agents-md-fragment.md"
        self.assertTrue(fragment.is_file())
        self.assertEqual(fragment.read_bytes(), AGENTS_FRAGMENT.read_bytes())

    def test_policy_update_preserves_existing_additions(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.output / "policy-update.json"
        self.assertTrue(path.is_file())
        policy = json.loads(path.read_text())
        self.assertEqual(policy["expectedVersion"], 7)
        self.assertEqual(len(policy["additions"]), 2)
        self.assertEqual(policy["additions"][0], self.EXISTING_ADDITION)
        self.assertEqual(
            policy["additions"][1],
            {
                "id": "redclaw-readonly-kube",
                "binary": "/usr/bin/curl",
                "host": self.CLUSTER_IP,
                "ports": [{"from": 443, "to": 443}],
            },
        )
        calls = self.occ_calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0]["argv"],
            [
                "agent",
                "policy",
                "get",
                "agt_test",
                "--namespace",
                "ns_test",
                "-o",
                "json",
            ],
        )
        self.assertEqual(calls[0]["OCC_URL"], "https://occ.example.test")
        self.assertEqual(
            calls[0]["OCC_SERVICE_KEY_FILE"], self.env["OCC_SERVICE_KEY_FILE"]
        )

    def test_policy_update_replaces_a_stale_addition(self):
        stale = {
            "id": "redclaw-readonly-kube",
            "binary": "/usr/bin/curl",
            "host": "old.example.test",
            "ports": [{"from": 443, "to": 443}],
        }
        self.write_occ_state(additions=[self.EXISTING_ADDITION, stale])
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        policy = json.loads((self.output / "policy-update.json").read_text())
        self.assertEqual(len(policy["additions"]), 2)
        self.assertEqual(policy["additions"][0], self.EXISTING_ADDITION)
        self.assertEqual(policy["additions"][1]["host"], self.CLUSTER_IP)

    def test_refuses_existing_output_directory(self):
        self.output.mkdir()
        marker = self.output / "keep"
        marker.write_text("keep")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.output), result.stderr)
        self.assertEqual([path.name for path in self.output.iterdir()], ["keep"])

    def test_fails_on_empty_token(self):
        self.write_oc_state(token="")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("grant-redclaw-readonly", result.stderr)
        self.assertFalse((self.output / "kubeconfig").exists())

    def test_fails_when_oc_is_not_authenticated(self):
        self.write_oc_state(whoami_fails=True)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("oc whoami failed", result.stderr)
        self.assertFalse(self.output.exists())

    def test_fails_when_occ_policy_get_fails(self):
        self.write_occ_state(fail=True)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.output / "policy-update.json").exists())

    def test_fails_on_invalid_occ_policy_json(self):
        self.write_occ_state(invalid_json=True)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid JSON", result.stderr)
        self.assertFalse((self.output / "policy-update.json").exists())


if __name__ == "__main__":
    unittest.main()
