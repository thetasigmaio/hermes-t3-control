from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import re
import secrets
import socket
import sys
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = (
    "t3_threads",
    "t3_thread_read",
    "t3_thread_create",
    "t3_thread_send",
    "t3_thread_set_mode",
    "t3_thread_implement_plan",
    "t3_turn_interrupt",
    "t3_session_stop",
    "t3_thread_wait",
    "t3_thread_respond",
    "t3_thread_settle",
)

RUNTIME_FILES = (
    "__init__.py",
    "auth.py",
    "schemas.py",
    "tools.py",
    "client.py",
    "continuation.py",
    "continuation_cli.py",
    "continuation_state.py",
    "continuation_transport.py",
)
FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "pathlib",
        "subprocess",
        "psutil",
        "sqlite3",
        "sqlalchemy",
        "psycopg",
        "pymysql",
        "requests",
        "httpx",
        "aiohttp",
        "urllib3",
        "urllib.request",
        "ftplib",
        "websocket",
        "websockets",
    }
)
FILE_CALLS = frozenset(
    {
        "open",
        "read_text",
        "write_text",
        "read_bytes",
        "write_bytes",
        "touch",
        "unlink",
        "rename",
        "mkdir",
        "rmdir",
        "iterdir",
        "glob",
        "rglob",
    }
)
OS_SIDE_EFFECT_CALLS = frozenset(
    {"getenv", "putenv", "system", "popen", "spawnl", "spawnle", "spawnv", "spawnve"}
)
ALTERNATE_NETWORK_PREFIXES = (
    "requests.",
    "httpx.",
    "aiohttp.",
    "urllib.request.",
    "urllib3.",
    "ftplib.",
    "websocket.",
    "websockets.",
)
SENSITIVE_NAME_RE = re.compile(
    r"(?:^|_)(?:api_?key|auth|authorization|credential|passwd|password|secret|token)(?:_|$)",
    re.IGNORECASE,
)
SECRET_PREFIX_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bsk-[A-Za-z0-9_-]{16,}",
        r"\b(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{16,}",
        r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{16,}",
        r"\bglpat-[A-Za-z0-9_-]{16,}",
        r"\bhf_[A-Za-z0-9]{16,}",
        r"\bxox[baprs]-[A-Za-z0-9-]{16,}",
        r"\bAIza[A-Za-z0-9_-]{20,}",
        r"\bAKIA[A-Z0-9]{16}",
        r"\bnpm_[A-Za-z0-9]{16,}",
        r"\bpypi-[A-Za-z0-9_-]{16,}",
        r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}",
        r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}={0,2}",
    )
)
BARE_CREDENTIAL_RE = re.compile(r"[A-Za-z0-9._~+/-]+={0,2}", re.ASCII)


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _module_matches(name: str, roots: frozenset[str]) -> bool:
    return any(name == root or name.startswith(f"{root}.") for root in roots)


def _target_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [name for item in node.elts for name in _target_names(item)]
    return []


def _is_documented_placeholder(value: str) -> bool:
    normalized = value.strip()
    lowered = normalized.lower()
    if re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", normalized) and any(
        marker in normalized for marker in ("TOKEN", "KEY", "SECRET", "PASSWORD")
    ):
        return True
    if normalized.startswith("<") and normalized.endswith(">"):
        return True
    return any(
        marker in lowered
        for marker in (
            "placeholder",
            "replace-me",
            "replace_me",
            "not-a-real",
            "not_a_real",
            "configured-",
            "configured_",
            "example-",
            "example_",
        )
    )


def _looks_like_common_credential(value: str) -> bool:
    return any(pattern.search(value) for pattern in SECRET_PREFIX_PATTERNS)


def _looks_like_bare_credential(value: str) -> bool:
    return (
        len(value) >= 20
        and not _is_documented_placeholder(value)
        and BARE_CREDENTIAL_RE.fullmatch(value) is not None
        and len(set(value)) >= 8
    )


class RuntimeSourcePolicy(ast.NodeVisitor):
    """AST policy for runtime capabilities; comments and strings are intentionally inert."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.aliases: dict[str, str] = {}
        self.violations: list[str] = []

    def _record(self, node: ast.AST, capability: str) -> None:
        self.violations.append(f"{self.filename}:{node.lineno}:{capability}")

    def _resolve(self, name: str | None) -> str | None:
        if name is None:
            return None
        first, separator, rest = name.partition(".")
        resolved = self.aliases.get(first, first)
        return f"{resolved}.{rest}" if separator else resolved

    def _inspect_sensitive_literal(
        self, node: ast.AST, value: object, target_names: list[str]
    ) -> None:
        if not isinstance(value, str):
            return
        if _looks_like_common_credential(value):
            self._record(node, "secret-like credential prefix")
            return
        if any(SENSITIVE_NAME_RE.search(name) for name in target_names) and (
            _looks_like_bare_credential(value)
        ):
            self._record(node, "bare credential literal in sensitive assignment")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            binding = alias.asname or alias.name.split(".", 1)[0]
            self.aliases[binding] = alias.name if alias.asname else binding
            allowed = (
                self.filename == "auth.py"
                and alias.name in {"pathlib", "subprocess"}
            ) or (
                self.filename == "continuation_state.py"
                and alias.name in {"pathlib", "sqlite3"}
            ) or (
                self.filename == "continuation_transport.py"
                and alias.name == "websockets"
            )
            if _module_matches(alias.name, FORBIDDEN_IMPORT_ROOTS) and not allowed:
                self._record(node, f"forbidden import {alias.name}")
            if alias.name == "http.client" and self.filename != "client.py":
                self._record(node, "HTTP transport outside client.py")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if (
            _module_matches(module, FORBIDDEN_IMPORT_ROOTS)
            or module in {"os", "socket"}
        ) and not (
            (self.filename == "auth.py" and module in {"pathlib", "subprocess"})
            or (
                self.filename == "continuation_state.py"
                and module in {"pathlib", "sqlite3", "os"}
            )
            or (
                self.filename == "continuation_transport.py"
                and module in {"socket", "websockets"}
            )
        ):
            self._record(node, f"forbidden direct import from {module}")
        if module == "http.client" and self.filename != "client.py":
            self._record(node, "HTTP transport outside client.py")
        if module == "agent.secret_scope" and (
            self.filename != "auth.py"
            or {alias.name for alias in node.names} != {"get_secret"}
        ):
            self._record(node, "profile secret resolver outside tools.py")
        if module == "builtins" and any(alias.name == "open" for alias in node.names):
            self._record(node, "direct file access")
        for alias in node.names:
            binding = alias.asname or alias.name
            self.aliases[binding] = f"{module}.{alias.name}" if module else alias.name
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        name = self._resolve(_dotted_name(node))
        if name == "os.environ":
            self._record(node, "direct environment access")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        target_names = [
            name for target in node.targets for name in _target_names(target)
        ]
        if isinstance(node.value, ast.Constant):
            self._inspect_sensitive_literal(node.value, node.value.value, target_names)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.value, ast.Constant):
            self._inspect_sensitive_literal(
                node.value, node.value.value, _target_names(node.target)
            )
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        if isinstance(node.value, ast.Constant):
            self._inspect_sensitive_literal(
                node.value, node.value.value, _target_names(node.target)
            )
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and isinstance(value, ast.Constant)
            ):
                self._inspect_sensitive_literal(value, value.value, [key.value])
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and _looks_like_common_credential(node.value):
            self._record(node, "secret-like credential prefix")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = self._resolve(_dotted_name(node.func))
        final_name = name.rsplit(".", 1)[-1] if name else ""
        if (name == "open" or final_name in FILE_CALLS) and not (
            (self.filename == "auth.py" and name == "os.open")
            or self.filename == "continuation_state.py"
        ):
            self._record(node, "direct file access")
        if name and name.startswith("socket.") and self.filename != "continuation_transport.py":
            self._record(node, "direct socket construction")
        if name and (
            name == "os.environ"
            or name.startswith("os.environ.")
            or final_name in OS_SIDE_EFFECT_CALLS
            and name.startswith("os.")
        ):
            self._record(node, "direct environment or process access")
        if name and any(name.startswith(prefix) for prefix in ALTERNATE_NETWORK_PREFIXES) and not (
            self.filename == "continuation_transport.py"
            and name.startswith(("websocket.", "websockets."))
        ):
            self._record(node, "alternate network client")
        if name and name.startswith("http.client."):
            allowed = self.filename == "client.py" and final_name in {
                "HTTPConnection",
                "HTTPSConnection",
            }
            if not allowed:
                self._record(node, "unapproved HTTP client construction")
        for keyword in node.keywords:
            if keyword.arg and isinstance(keyword.value, ast.Constant):
                self._inspect_sensitive_literal(
                    keyword.value, keyword.value.value, [keyword.arg]
                )
        self.generic_visit(node)


def runtime_source_violations(source: str, filename: str) -> list[str]:
    policy = RuntimeSourcePolicy(filename)
    policy.visit(ast.parse(source, filename=filename))
    return sorted(set(policy.violations))


def load_plugin(*, fresh: bool = False):
    name = "hermes_t3_control_test_plugin"
    if fresh:
        for module_name in tuple(sys.modules):
            if module_name == name or module_name.startswith(f"{name}."):
                sys.modules.pop(module_name, None)
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    if spec is None or spec.loader is None:
        raise AssertionError("plugin spec unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakePluginContext:
    def __init__(self) -> None:
        self.registrations: list[dict] = []

    def register_tool(self, **kwargs) -> None:
        self.registrations.append(kwargs)


class RegistrationTests(unittest.TestCase):
    def test_register_is_exact_synchronous_non_overriding_and_no_io(self) -> None:
        ctx = FakePluginContext()
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network attempted")):
            plugin = load_plugin(fresh=True)
            plugin.register(ctx)
        self.assertEqual(tuple(item["name"] for item in ctx.registrations), EXPECTED_TOOLS)
        self.assertEqual({item["toolset"] for item in ctx.registrations}, {"t3_control"})
        self.assertEqual({item["override"] for item in ctx.registrations}, {False})
        self.assertEqual({item["is_async"] for item in ctx.registrations}, {False})
        self.assertEqual(
            {tuple(item["requires_env"]) for item in ctx.registrations},
            {()},
        )
        check_ids = {id(item["check_fn"]) for item in ctx.registrations}
        self.assertEqual(len(check_ids), 1)
        for item in ctx.registrations:
            self.assertEqual(item["schema"]["name"], item["name"])
            self.assertFalse(item["schema"]["parameters"]["additionalProperties"])
            result = json.loads(item["handler"]({"unexpected": True}, additive_context=True))
            self.assertEqual(result["error_code"], "invalid_input")

    def test_manifest_and_public_schemas_are_exact(self) -> None:
        manifest = json.loads((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_version"], 1)
        self.assertEqual(manifest["api_version"], 1)
        self.assertEqual(manifest["version"], "1.3.1")
        self.assertEqual(
            manifest["description"],
            "Control T3 work from Hermes with bounded tools and operation-scoped local authentication; core thread lifecycle verified end-to-end with Codex.",
        )
        self.assertEqual(manifest["license"], "MIT")
        self.assertEqual(
            manifest["homepage"], "https://github.com/thetasigmaio/hermes-t3-control"
        )
        self.assertEqual(tuple(manifest["provides_tools"]), EXPECTED_TOOLS)
        self.assertEqual(manifest["python_dependencies"], ["websockets>=15,<16"])
        self.assertEqual(
            set(manifest["config_schema"]),
            {
                "auth_mode",
                "base_url",
                "t3_base_dir",
                "default_runtime_mode",
                "default_instance_id",
                "default_model",
                "default_reasoning_effort",
                "default_model_aliases",
                "continuation_enabled",
                "continuation_profile",
                "continuation_max_queue_rows",
                "continuation_max_reconnects",
                "continuation_receipt_timeout_seconds",
            },
        )
        self.assertEqual(
            manifest["config_schema"]["default_runtime_mode"]["default"],
            "approval-required",
        )
        auth_description = manifest["config_schema"]["auth_mode"]["description"]
        self.assertIn("valid profile token selects external-token", auth_description)
        self.assertIn("invalid token configuration fails closed", auth_description)
        aliases = manifest["config_schema"]["default_model_aliases"]
        self.assertEqual(aliases["type"], "array")
        self.assertEqual(aliases["items"], {"type": "string"})
        self.assertIn("exact configured instance", aliases["description"])
        self.assertIn("another explicit instance remains literal", aliases["description"])
        self.assertEqual(manifest.get("requires_env", []), [])
        plugin = load_plugin()
        self.assertEqual(tuple(plugin.TOOL_NAMES), EXPECTED_TOOLS)
        for schema in plugin.SCHEMAS.values():
            properties = schema["parameters"]["properties"]
            self.assertFalse({"command_id", "thread_id_override", "message_id"} & set(properties))
        self.assertEqual(
            plugin.SCHEMAS["t3_thread_implement_plan"]["parameters"]["properties"].keys(),
            {"thread_id", "plan_id"},
        )
        self.assertIn("oneOf", plugin.SCHEMAS["t3_thread_set_mode"]["parameters"])
        self.assertFalse(any("dispatch" in name for name in plugin.TOOL_NAMES))

    def test_passive_check_keeps_tokenless_local_mode_discoverable_without_io(self) -> None:
        plugin = load_plugin()
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network attempted")):
            self.assertTrue(plugin.check_t3_available())

    def test_plugin_runtime_source_obeys_capability_policy(self) -> None:
        sources = {
            name: (ROOT / name).read_text(encoding="utf-8") for name in RUNTIME_FILES
        }
        for name, source in sources.items():
            with self.subTest(runtime_file=name):
                self.assertEqual(runtime_source_violations(source, name), [])
        combined = "\n".join(sources.values())
        self.assertNotRegex(combined, r"\.jsonl\b|bootstrap\.createThread")
        self.assertNotIn("shell=True", combined)
        self.assertEqual(sources["auth.py"].count('"/proc/"'), 1)

    def test_runtime_source_policy_rejects_capability_sentinels(self) -> None:
        blocked = {
            "open": "open('state.json').read()",
            "pathlib": "from pathlib import Path\nPath('state').read_text()",
            "environment": "import os\nvalue = os.environ['TOKEN']",
            "sqlite": "import sqlite3\nsqlite3.connect('state.db')",
            "subprocess": "import subprocess\nsubprocess.run(['tool'])",
            "socket": "import socket as net\nnet.socket()",
            "requests": "import requests\nrequests.get('http://127.0.0.1')",
            "urllib": "from urllib.request import urlopen\nurlopen('http://127.0.0.1')",
            "http_outside_client": "import http.client\nhttp.client.HTTPConnection('127.0.0.1')",
        }
        for capability, source in blocked.items():
            with self.subTest(capability=capability):
                self.assertTrue(runtime_source_violations(source, "tools.py"))

        intentional_client = (
            "import http.client\n"
            "connection = http.client.HTTPConnection('127.0.0.1', 9137, timeout=1)\n"
        )
        intentional_resolver = (
            "def resolve():\n"
            "    from agent.secret_scope import get_secret\n"
            "    return get_secret('PROFILE_TOKEN')\n"
        )
        inert_text = (
            "# open('ignored') and socket.socket()\n"
            "EXAMPLE = 'subprocess.run and os.environ are documentation only'\n"
        )
        self.assertEqual(
            runtime_source_violations(intentional_client, "client.py"), []
        )
        self.assertEqual(runtime_source_violations(intentional_resolver, "auth.py"), [])
        intentional_state = (
            "import sqlite3\nfrom pathlib import Path\n"
            "sqlite3.connect(Path('state.db'))\nPath('key').read_bytes()\n"
        )
        intentional_stream = (
            "import socket\nimport websockets\n"
            "socket.socket()\nwebsockets.connect('ws://127.0.0.1')\n"
        )
        self.assertEqual(
            runtime_source_violations(intentional_state, "continuation_state.py"), []
        )
        self.assertEqual(
            runtime_source_violations(
                intentional_stream, "continuation_transport.py"
            ),
            [],
        )
        self.assertTrue(runtime_source_violations(intentional_state, "tools.py"))
        self.assertTrue(runtime_source_violations(intentional_stream, "client.py"))
        self.assertEqual(runtime_source_violations(inert_text, "tools.py"), [])

    def test_runtime_source_policy_rejects_disposable_secret_literal_sentinels(self) -> None:
        bare_value = secrets.token_urlsafe(24)
        prefix_tail = secrets.token_hex(20)
        prefixed_values = {
            "provider": "".join(("s", "k", "-", prefix_tail)),
            "github": "".join(("g", "h", "p", "_", prefix_tail)),
            "authorization": "".join(("Bear", "er", " ", prefix_tail)),
        }
        blocked_sources = {
            "sensitive_assignment": f"SERVICE_TOKEN = {bare_value!r}",
            "sensitive_keyword": f"configure(api_key={bare_value!r})",
            "sensitive_mapping": f"config = {{'password': {bare_value!r}}}",
            **{
                f"prefix_{name}": f"VALUE = {value!r}"
                for name, value in prefixed_values.items()
            },
        }
        for sentinel, source in blocked_sources.items():
            with self.subTest(sentinel=sentinel):
                self.assertTrue(runtime_source_violations(source, "tools.py"))

        allowed_sources = {
            "profile_name": "TOKEN_ENV = 'T3_ORCHESTRATION_TOKEN'",
            "documented_placeholder": "API_KEY = '<configured-api-key>'",
            "non_secret_description": (
                "DESCRIPTION = 'Credential is provided by the profile resolver.'"
            ),
        }
        for sentinel, source in allowed_sources.items():
            with self.subTest(sentinel=sentinel):
                self.assertEqual(runtime_source_violations(source, "tools.py"), [])


if __name__ == "__main__":
    unittest.main()
