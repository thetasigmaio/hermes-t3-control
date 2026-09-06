from __future__ import annotations

import pathlib
import tempfile
import unittest

import continuation
from tests.test_registration import EXPECTED_TOOLS, load_plugin


class RegistrationContext:
    profile_name = "default"

    def __init__(self, data_dir: pathlib.Path, *, enabled: bool):
        self.state = type("State", (), {"data_dir": data_dir})()
        self.enabled = enabled
        self.tools = []
        self.cli = []
        self.gateway_tasks = []

    def get_config(self, name, default=None):
        if name == "continuation_enabled":
            return self.enabled
        return default

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_cli_command(self, **kwargs):
        self.cli.append(kwargs)

    def register_gateway_task(self, factory, **kwargs):
        self.gateway_tasks.append((factory, kwargs))
        return "gateway-task-handle"


class ContinuationRegistrationTests(unittest.TestCase):
    def test_disabled_registration_is_inert_but_operator_cli_is_available(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = pathlib.Path(temporary) / "uncreated"
            ctx = RegistrationContext(data_dir, enabled=False)
            load_plugin(fresh=True).register(ctx)

            self.assertEqual(tuple(item["name"] for item in ctx.tools), EXPECTED_TOOLS)
            self.assertEqual([item["name"] for item in ctx.cli], ["t3-continuation"])
            self.assertEqual(ctx.gateway_tasks, [])
            self.assertFalse(data_dir.exists())

    def test_enabled_registration_uses_host_gateway_task_factory(self):
        with tempfile.TemporaryDirectory() as temporary:
            ctx = RegistrationContext(pathlib.Path(temporary), enabled=True)
            result = continuation.register_continuation_lifecycle(ctx)

            self.assertEqual(result, "gateway-task-handle")
            self.assertEqual(len(ctx.gateway_tasks), 1)
            factory, options = ctx.gateway_tasks[0]
            self.assertEqual(options, {"name": "t3-continuation"})
            coroutine = factory()
            self.assertEqual(coroutine.cr_code.co_name, "run_continuation_worker")
            coroutine.close()

    def test_enabled_registration_fails_inert_on_older_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            ctx = RegistrationContext(pathlib.Path(temporary), enabled=True)
            ctx.register_gateway_task = None
            with self.assertLogs(continuation.logger, level="WARNING"):
                self.assertIsNone(continuation.register_continuation_lifecycle(ctx))


if __name__ == "__main__":
    unittest.main()
