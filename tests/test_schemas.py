from __future__ import annotations

import unittest

import schemas


EXPECTED_TOOLS = {
    "t3_threads",
    "t3_thread_read",
    "t3_thread_create",
    "t3_thread_send",
    "t3_thread_wait",
    "t3_thread_respond",
    "t3_thread_set_mode",
    "t3_thread_implement_plan",
    "t3_turn_interrupt",
    "t3_session_stop",
}


class AgentFacingSchemaTests(unittest.TestCase):
    def properties(self, tool_name: str) -> dict:
        self.assertIn(tool_name, schemas.SCHEMAS)
        schema = schemas.SCHEMAS[tool_name]
        self.assertEqual(schema["name"], tool_name)
        self.assertFalse(schema["parameters"]["additionalProperties"])
        return schema["parameters"]["properties"]

    def test_exactly_ten_public_tools_include_wait_and_respond(self) -> None:
        self.assertEqual(len(schemas.SCHEMAS), 10)
        self.assertEqual(set(schemas.SCHEMAS), EXPECTED_TOOLS)
        self.assertEqual(set(schemas.TOOL_NAMES), EXPECTED_TOOLS)

    def test_threads_schema_pins_compact_default_and_bounded_filters(self) -> None:
        properties = self.properties("t3_threads")
        self.assertEqual(
            set(properties),
            {
                "view",
                "project",
                "workspace",
                "title_query",
                "lifecycle",
                "updated_within_minutes",
                "limit",
                "require_one",
            },
        )
        self.assertEqual(
            properties["view"],
            {"type": "string", "enum": ["compact", "raw"], "default": "compact"},
        )
        self.assertEqual(
            properties["lifecycle"]["enum"],
            [
                "idle",
                "starting",
                "running",
                "ready",
                "interrupted",
                "stopped",
                "error",
                "blocked",
            ],
        )
        self.assertEqual(
            properties["updated_within_minutes"],
            {"type": "integer", "minimum": 1, "maximum": 10_080},
        )
        self.assertEqual(
            properties["limit"],
            {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        )
        self.assertEqual(
            properties["require_one"],
            {"type": "boolean", "default": False},
        )
        self.assertEqual(schemas.SCHEMAS["t3_threads"]["parameters"]["required"], [])

    def test_read_and_send_schema_pin_agent_defaults_without_removing_legacy_fields(self) -> None:
        read = self.properties("t3_thread_read")
        self.assertEqual(
            set(read),
            {"thread_id", "view", "turn_limit", "before_cursor"},
        )
        self.assertEqual(
            read["view"],
            {"type": "string", "enum": ["material", "raw"], "default": "material"},
        )
        self.assertEqual(read["turn_limit"]["maximum"], 150)
        self.assertEqual(
            schemas.SCHEMAS["t3_thread_read"]["parameters"]["required"],
            ["thread_id"],
        )

        send = self.properties("t3_thread_send")
        self.assertEqual(set(send), {"thread_id", "message", "busy_policy"})
        self.assertEqual(
            send["busy_policy"],
            {"type": "string", "enum": ["reject", "queue"], "default": "reject"},
        )
        self.assertEqual(
            schemas.SCHEMAS["t3_thread_send"]["parameters"]["required"],
            ["thread_id", "message"],
        )

    def test_wait_schema_is_synchronous_and_hard_bounded_to_thirty_seconds(self) -> None:
        wait = self.properties("t3_thread_wait")
        self.assertEqual(
            set(wait),
            {"thread_id", "after_thread_sequence", "until", "timeout_seconds"},
        )
        self.assertEqual(
            wait["after_thread_sequence"],
            {"type": "integer", "minimum": 0},
        )
        self.assertEqual(
            wait["until"],
            {
                "type": "string",
                "enum": ["change", "running", "blocked", "terminal", "error"],
                "default": "terminal",
            },
        )
        self.assertEqual(
            wait["timeout_seconds"],
            {"type": "integer", "minimum": 0, "maximum": 30, "default": 0},
        )
        self.assertEqual(
            schemas.SCHEMAS["t3_thread_wait"]["parameters"]["required"],
            ["thread_id"],
        )

    def test_respond_schema_is_an_exact_approval_or_user_input_union(self) -> None:
        respond_schema = schemas.SCHEMAS.get("t3_thread_respond")
        self.assertIsNotNone(respond_schema)
        assert respond_schema is not None
        parameters = respond_schema["parameters"]
        properties = parameters["properties"]
        self.assertEqual(
            set(properties),
            {"thread_id", "request_id", "decision", "answers"},
        )
        self.assertEqual(
            properties["decision"]["enum"],
            ["accept", "acceptForSession", "decline", "cancel"],
        )
        self.assertEqual(properties["answers"]["type"], "object")
        self.assertGreaterEqual(properties["answers"].get("minProperties", 0), 1)
        self.assertEqual(properties["answers"]["maxProperties"], 64)
        self.assertEqual(
            properties["answers"]["propertyNames"],
            {"type": "string", "minLength": 1, "maxLength": 512},
        )
        self.assertEqual(
            properties["answers"]["additionalProperties"],
            {
                "oneOf": [
                    {"type": "string", "minLength": 0, "maxLength": 120_000},
                    {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "minLength": 0,
                            "maxLength": 120_000,
                        },
                        "maxItems": 64,
                    },
                    {
                        "type": "number",
                        "minimum": -(2**53 - 1),
                        "maximum": 2**53 - 1,
                    },
                    {"type": "boolean"},
                    {"type": "null"},
                ]
            },
        )
        self.assertEqual(parameters["required"], ["thread_id", "request_id"])
        self.assertEqual(
            parameters["oneOf"],
            [
                {"required": ["decision"]},
                {"required": ["answers"]},
            ],
        )

    def test_set_mode_schema_warns_about_full_access(self) -> None:
        description = schemas.SCHEMAS["t3_thread_set_mode"]["description"]
        self.assertIn("full-access", description)
        self.assertIn("modify or delete files without approval", description)

    def test_every_public_string_remains_explicitly_preflight_compatible(self) -> None:
        string_paths: set[tuple[str, ...]] = set()

        def walk(node: object, path: tuple[str, ...]) -> None:
            if isinstance(node, dict):
                if node.get("type") == "string":
                    string_paths.add(path)
                    self.assertTrue(
                        "enum" in node
                        or (
                            node.get("minLength") in {0, 1}
                            and "maxLength" in node
                        ),
                        f"Unbounded or nullable public string schema at {'.'.join(path)}",
                    )
                for key, value in node.items():
                    walk(value, (*path, str(key)))
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, (*path, str(index)))

        for tool_name, schema in schemas.SCHEMAS.items():
            walk(schema["parameters"], (tool_name,))

        required_new_paths = {
            ("t3_threads", "properties", "view"),
            ("t3_threads", "properties", "project"),
            ("t3_threads", "properties", "workspace"),
            ("t3_threads", "properties", "title_query"),
            ("t3_threads", "properties", "lifecycle"),
            ("t3_thread_read", "properties", "view"),
            ("t3_thread_send", "properties", "busy_policy"),
            ("t3_thread_wait", "properties", "thread_id"),
            ("t3_thread_wait", "properties", "until"),
            ("t3_thread_respond", "properties", "thread_id"),
            ("t3_thread_respond", "properties", "request_id"),
            ("t3_thread_respond", "properties", "decision"),
        }
        self.assertTrue(required_new_paths <= string_paths)


if __name__ == "__main__":
    unittest.main()
