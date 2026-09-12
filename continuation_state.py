"""Durable, profile-local state for allowlisted T3 continuation bindings."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


SCHEMA_VERSION = 4
MAX_IDENTIFIER_CHARS = 512
MAX_SESSION_KEY_CHARS = 512
MAX_SESSION_ID_CHARS = 256
MAX_EVENT_BYTES = 32 * 1024
MAX_RECEIPT_BYTES = 8 * 1024
MAX_QUEUE_ROWS = 128
AUTHORITY_FIELDS = (
    "binding_id",
    "profile_name",
    "t3_thread_id",
    "t3_owner_id",
    "t3_environment_id",
    "hermes_session_key",
    "hermes_session_id",
    "platform",
    "user_id",
    "chat_id",
    "topic_id",
    "sunsama_task_id",
    "source_identity",
    "followup_scope",
    "max_continuations",
)
RENEWAL_SOURCE_FIELDS = (
    "profile_name",
    "t3_thread_id",
    "t3_owner_id",
    "t3_environment_id",
)
RENEWAL_DESTINATION_FIELDS = (
    "hermes_session_key",
    "platform",
    "user_id",
    "chat_id",
    "topic_id",
)
V1_BINDING_COLUMNS = (
    "binding_id",
    "profile_name",
    "t3_thread_id",
    "t3_owner_id",
    "t3_environment_id",
    "hermes_session_key",
    "hermes_session_id",
    "platform",
    "user_id",
    "chat_id",
    "topic_id",
    "sunsama_task_id",
    "source_identity",
    "followup_scope",
    "max_continuations",
    "state",
    "cursor_sequence",
    "created_at",
    "updated_at",
)
V2_BINDING_COLUMNS = V1_BINDING_COLUMNS + ("binding_schema_version",)
BINDING_COLUMNS = V2_BINDING_COLUMNS + ("baseline_captured",)
EVENT_COLUMNS = (
    "binding_id",
    "source_sequence",
    "source_event_id",
    "source_turn_id",
    "event_kind",
    "occurred_at",
    "envelope_json",
    "envelope_mac",
    "status",
    "attempts",
    "busy_attempts",
    "receipt_json",
    "last_error_code",
    "created_at",
    "updated_at",
)


class ContinuationStateError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean(value: Any, name: str, *, maximum: int = MAX_IDENTIFIER_CHARS) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"{name} is missing or invalid")
    return value


def _clean_optional(value: Any, name: str, *, maximum: int = MAX_IDENTIFIER_CHARS) -> str:
    if value is None or value == "":
        return ""
    return _clean(value, name, maximum=maximum)


def _canonical(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ValueError("continuation value is not JSON serializable") from exc
    return encoded


@dataclass(frozen=True)
class Binding:
    binding_id: str
    profile_name: str
    t3_thread_id: str
    t3_owner_id: str
    t3_environment_id: str
    hermes_session_key: str
    hermes_session_id: str
    platform: str
    user_id: str
    chat_id: str
    topic_id: str
    sunsama_task_id: str
    source_identity: str
    followup_scope: str
    max_continuations: int
    state: str
    cursor_sequence: int
    created_at: str
    updated_at: str
    binding_schema_version: int
    baseline_captured: bool

    def __post_init__(self) -> None:
        if self.binding_schema_version != SCHEMA_VERSION:
            raise ContinuationStateError("binding schema discriminator is invalid")


class ContinuationStore:
    """Small SQLite ledger with atomic cursor/event insertion."""

    def __init__(self, data_dir: str | Path, *, max_queue_rows: int = MAX_QUEUE_ROWS):
        if isinstance(max_queue_rows, bool) or not 1 <= int(max_queue_rows) <= 1024:
            raise ValueError("max_queue_rows must be between 1 and 1024")
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "continuation.sqlite3"
        self.key_path = self.data_dir / "continuation.hmac"
        self.max_queue_rows = int(max_queue_rows)
        self._lock = threading.RLock()

    def initialize(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data_dir, 0o700)
        try:
            with self._connect() as db:
                self._initialize_connection(db)

        except ContinuationStateError:
            raise
        except sqlite3.Error as exc:
            raise ContinuationStateError(
                "continuation state database operation failed"
            ) from exc
        os.chmod(self.db_path, 0o600)
        self._key()

    def _initialize_connection(self, db: sqlite3.Connection) -> None:
        objects = {
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if not objects:
            self._create_schema_current(db)
        elif "meta" not in objects:
            raise ContinuationStateError("continuation schema metadata is missing")
        else:
            row = db.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                raise ContinuationStateError("continuation schema version is missing")
            try:
                version = int(row["value"])
            except (TypeError, ValueError) as exc:
                raise ContinuationStateError(
                    "continuation schema version is invalid"
                ) from exc
            if version == 1:
                self._migrate_v1_to_current(db)
            elif version == 2:
                self._migrate_v2_to_current(db)
            elif version == 3:
                self._migrate_v3_to_current(db)
            elif version == SCHEMA_VERSION:
                self._validate_schema(db)
            elif version > SCHEMA_VERSION:
                raise ContinuationStateError(
                    "continuation schema is newer than this plugin"
                )
            else:
                raise ContinuationStateError("continuation schema version is unsupported")

    @staticmethod
    def _create_bindings_table(db: sqlite3.Connection, name: str = "bindings") -> None:
        if name not in {"bindings", "bindings_v2", "bindings_v3"}:
            raise ValueError("unsupported bindings table name")
        db.execute(
            f"""CREATE TABLE {name} (
                binding_id TEXT PRIMARY KEY,
                profile_name TEXT NOT NULL,
                t3_thread_id TEXT NOT NULL,
                t3_owner_id TEXT NOT NULL,
                t3_environment_id TEXT NOT NULL,
                hermes_session_key TEXT NOT NULL,
                hermes_session_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                user_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                sunsama_task_id TEXT NOT NULL,
                source_identity TEXT NOT NULL,
                followup_scope TEXT NOT NULL,
                max_continuations INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('active','paused','stopped','cancelled')),
                cursor_sequence INTEGER NOT NULL DEFAULT 0 CHECK(cursor_sequence >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                binding_schema_version INTEGER NOT NULL CHECK(binding_schema_version = 4),
                baseline_captured INTEGER NOT NULL DEFAULT 0 CHECK(baseline_captured IN (0,1))
            )"""
        )

    @staticmethod
    def _create_events_table(db: sqlite3.Connection) -> None:
        db.execute(
            """CREATE TABLE events (
                binding_id TEXT NOT NULL REFERENCES bindings(binding_id),
                source_sequence INTEGER NOT NULL CHECK(source_sequence >= 0),
                source_event_id TEXT NOT NULL,
                source_turn_id TEXT NOT NULL,
                event_kind TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                envelope_json TEXT NOT NULL,
                envelope_mac TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN
                    ('queued','dispatching','completed','acknowledged','cancelled','blocked','uncertain')),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                busy_attempts INTEGER NOT NULL DEFAULT 0 CHECK(busy_attempts >= 0),
                receipt_json TEXT,
                last_error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(binding_id, source_sequence),
                UNIQUE(binding_id, source_event_id),
                UNIQUE(binding_id, event_kind, source_turn_id)
            )"""
        )

    @staticmethod
    def _create_v2_additions(db: sqlite3.Connection) -> None:
        db.execute(
            """CREATE TABLE binding_lineage (
                predecessor_binding_id TEXT PRIMARY KEY REFERENCES bindings(binding_id),
                successor_binding_id TEXT NOT NULL UNIQUE REFERENCES bindings(binding_id),
                renewed_at TEXT NOT NULL
            )"""
        )
        db.execute(
            """CREATE UNIQUE INDEX bindings_live_thread_idx
            ON bindings(t3_thread_id) WHERE state IN ('active','paused')"""
        )
        db.execute(
            "CREATE INDEX events_dispatch_idx ON events(status, updated_at, source_sequence)"
        )

    def _create_schema_current(self, db: sqlite3.Connection) -> None:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            self._create_bindings_table(db)
            self._create_events_table(db)
            self._create_v2_additions(db)
            db.execute(
                "INSERT INTO meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise ContinuationStateError("continuation schema foreign keys are invalid")
            self._create_extensions(db)
            self._validate_schema(db)
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise

    @staticmethod
    def _table_columns(db: sqlite3.Connection, name: str) -> tuple[str, ...]:
        return tuple(row["name"] for row in db.execute(f"PRAGMA table_info({name})"))

    @staticmethod
    def _index_columns(db: sqlite3.Connection, name: str) -> tuple[str, ...]:
        return tuple(row["name"] for row in db.execute(f"PRAGMA index_info({name})"))

    def _unique_index_columns(
        self, db: sqlite3.Connection, table: str
    ) -> set[tuple[str, ...]]:
        return {
            self._index_columns(db, row["name"])
            for row in db.execute(f"PRAGMA index_list({table})")
            if row["unique"]
        }

    def _validate_schema_v1(self, db: sqlite3.Connection) -> None:
        tables = {
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if tables != {"meta", "bindings", "events"}:
            raise ContinuationStateError("continuation schema v1 is not canonical")
        if self._table_columns(db, "bindings") != V1_BINDING_COLUMNS:
            raise ContinuationStateError("continuation bindings schema v1 is not canonical")
        if self._table_columns(db, "events") != EVENT_COLUMNS:
            raise ContinuationStateError("continuation events schema v1 is not canonical")
        if self._unique_index_columns(db, "bindings") != {
            ("binding_id",),
            ("t3_thread_id",),
        } or self._unique_index_columns(db, "events") != {
            ("binding_id", "source_sequence"),
            ("binding_id", "source_event_id"),
            ("binding_id", "event_kind", "source_turn_id"),
        }:
            raise ContinuationStateError("continuation schema v1 indexes are not canonical")
        if self._index_columns(db, "events_dispatch_idx") != (
            "status",
            "updated_at",
            "source_sequence",
        ):
            raise ContinuationStateError("continuation schema v1 indexes are not canonical")
        foreign_keys = db.execute("PRAGMA foreign_key_list(events)").fetchall()
        if len(foreign_keys) != 1 or any(
            (
                foreign_keys[0]["table"] != "bindings",
                foreign_keys[0]["from"] != "binding_id",
                foreign_keys[0]["to"] != "binding_id",
            )
        ):
            raise ContinuationStateError("continuation schema v1 foreign key is not canonical")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ContinuationStateError("continuation database integrity check failed")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ContinuationStateError("continuation database has invalid foreign keys")

    def _validate_schema(self, db: sqlite3.Connection, *, version: int = SCHEMA_VERSION) -> None:
        tables = {
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        expected_tables = {"meta", "bindings", "events", "binding_lineage"}
        if version == 4:
            expected_tables |= {"source_reservations", "notification_targets", "notifications"}
        if tables != expected_tables:
            raise ContinuationStateError("continuation schema is not canonical")
        if version == 4:
            self._validate_extensions(db)
        if self._table_columns(db, "bindings") != (V2_BINDING_COLUMNS if version == 2 else BINDING_COLUMNS):
            raise ContinuationStateError("continuation bindings schema is not canonical")
        discriminator = next(
            (
                row
                for row in db.execute("PRAGMA table_info(bindings)")
                if row["name"] == "binding_schema_version"
            ),
            None,
        )
        bindings_sql_row = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='bindings'"
        ).fetchone()
        bindings_sql = (
            ""
            if bindings_sql_row is None
            else "".join(bindings_sql_row["sql"].lower().split())
        )
        if (
            discriminator is None
            or discriminator["type"].upper() != "INTEGER"
            or not discriminator["notnull"]
            or discriminator["dflt_value"] is not None
            or f"binding_schema_versionintegernotnullcheck(binding_schema_version={version})"
            not in bindings_sql
        ):
            raise ContinuationStateError(
                "continuation binding discriminator is not canonical"
            )
        if version >= 3:
            baseline = next(row for row in db.execute("PRAGMA table_info(bindings)")
                            if row["name"] == "baseline_captured")
            if (baseline["type"].upper() != "INTEGER" or not baseline["notnull"]
                    or baseline["dflt_value"] != "0"
                    or "baseline_capturedintegernotnulldefault0check(baseline_capturedin(0,1))" not in bindings_sql):
                raise ContinuationStateError("continuation baseline discriminator is not canonical")
        if self._table_columns(db, "events") != EVENT_COLUMNS:
            raise ContinuationStateError("continuation events schema is not canonical")
        if self._table_columns(db, "binding_lineage") != (
            "predecessor_binding_id",
            "successor_binding_id",
            "renewed_at",
        ):
            raise ContinuationStateError("continuation lineage schema is not canonical")
        binding_indexes = {
            row["name"]: row for row in db.execute("PRAGMA index_list(bindings)")
        }
        live_index = binding_indexes.get("bindings_live_thread_idx")
        live_sql_row = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='bindings_live_thread_idx'"
        ).fetchone()
        live_sql = "" if live_sql_row is None else "".join(live_sql_row["sql"].lower().split())
        if (
            live_index is None
            or not live_index["unique"]
            or not live_index["partial"]
            or self._index_columns(db, "bindings_live_thread_idx") != ("t3_thread_id",)
            or "wherestatein('active','paused')" not in live_sql
        ):
            raise ContinuationStateError("continuation schema indexes are not canonical")
        if self._unique_index_columns(db, "binding_lineage") != {
            ("predecessor_binding_id",),
            ("successor_binding_id",),
        }:
            raise ContinuationStateError("continuation lineage indexes are not canonical")
        if self._index_columns(db, "events_dispatch_idx") != (
            "status",
            "updated_at",
            "source_sequence",
        ):
            raise ContinuationStateError("continuation schema indexes are not canonical")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ContinuationStateError("continuation database integrity check failed")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ContinuationStateError("continuation database has invalid foreign keys")
        event_foreign_keys = {
            (row["from"], row["table"], row["to"])
            for row in db.execute("PRAGMA foreign_key_list(events)")
        }
        lineage_foreign_keys = {
            (row["from"], row["table"], row["to"])
            for row in db.execute("PRAGMA foreign_key_list(binding_lineage)")
        }
        if event_foreign_keys != {("binding_id", "bindings", "binding_id")} or lineage_foreign_keys != {
            ("predecessor_binding_id", "bindings", "binding_id"),
            ("successor_binding_id", "bindings", "binding_id"),
        }:
            raise ContinuationStateError("continuation schema foreign keys are not canonical")
        stored_version = db.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        if stored_version is None or stored_version["value"] != str(version):
            raise ContinuationStateError("continuation schema version is not canonical")


    def _migrate_v1_to_current(self, db: sqlite3.Connection) -> None:
        self._validate_schema_v1(db)
        db.execute("PRAGMA foreign_keys=OFF")
        if db.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
            raise ContinuationStateError("continuation migration could not suspend foreign keys")
        try:
            db.execute("BEGIN IMMEDIATE")
            self._create_bindings_table(db, "bindings_v2")
            old_columns = ",".join(V1_BINDING_COLUMNS)
            new_columns = ",".join(BINDING_COLUMNS)
            db.execute(
                f"INSERT INTO bindings_v2({new_columns}) "
                f"SELECT {old_columns},?,cursor_sequence>0 FROM bindings",
                (SCHEMA_VERSION,),
            )
            db.execute("DROP TABLE bindings")
            db.execute("ALTER TABLE bindings_v2 RENAME TO bindings")
            db.execute(
                """CREATE TABLE binding_lineage (
                    predecessor_binding_id TEXT PRIMARY KEY REFERENCES bindings(binding_id),
                    successor_binding_id TEXT NOT NULL UNIQUE REFERENCES bindings(binding_id),
                    renewed_at TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE UNIQUE INDEX bindings_live_thread_idx
                ON bindings(t3_thread_id) WHERE state IN ('active','paused')"""
            )
            changed = db.execute(
                "UPDATE meta SET value=? WHERE key='schema_version'",
                (str(SCHEMA_VERSION),),
            ).rowcount
            if changed != 1:
                raise ContinuationStateError(
                    "continuation migration could not record schema version"
                )
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise ContinuationStateError(
                    "continuation migration produced invalid foreign keys"
                )
            self._create_extensions(db)
            self._validate_schema(db)
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.execute("PRAGMA foreign_keys=ON")
        self._validate_schema(db)

    def _migrate_v2_to_current(self, db: sqlite3.Connection) -> None:
        # Only the canonical public v2 schema is supported. Experimental local
        # variants sharing its version number must fail closed without mutation.
        self._validate_schema(db, version=2)
        db.execute("PRAGMA foreign_keys=OFF")
        if db.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
            raise ContinuationStateError("continuation migration could not suspend foreign keys")
        try:
            db.execute("BEGIN IMMEDIATE")
            self._create_bindings_table(db, "bindings_v3")
            db.execute(
                f"INSERT INTO bindings_v3({','.join(BINDING_COLUMNS)}) "
                f"SELECT {','.join(V1_BINDING_COLUMNS)},?,cursor_sequence>0 FROM bindings",
                (SCHEMA_VERSION,),
            )
            db.execute("DROP TABLE bindings")
            db.execute("ALTER TABLE bindings_v3 RENAME TO bindings")
            db.execute("CREATE UNIQUE INDEX bindings_live_thread_idx ON bindings(t3_thread_id) "
                       "WHERE state IN ('active','paused')")
            db.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            self._create_extensions(db)
            self._validate_schema(db)
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.execute("PRAGMA foreign_keys=ON")

    @contextmanager
    def _connect(self, *, nonblocking: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(
            self.db_path,
            timeout=0.0 if nonblocking else 5.0,
            isolation_level=None,
        )
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute(f"PRAGMA busy_timeout={0 if nonblocking else 5000}")
            db.execute("PRAGMA synchronous=FULL")
            yield db
        finally:
            db.close()

    def _key(self) -> bytes:
        try:
            value = self.key_path.read_bytes()
        except FileNotFoundError:
            value = secrets.token_bytes(32)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(self.key_path, flags, 0o600)
            try:
                os.write(descriptor, value)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        if len(value) != 32:
            raise ContinuationStateError("continuation authentication key is invalid")
        os.chmod(self.key_path, 0o600)
        return value

    def sign(self, envelope: Mapping[str, Any]) -> str:
        return hmac.new(self._key(), _canonical(envelope), hashlib.sha256).hexdigest()

    def verify(self, envelope: Mapping[str, Any], signature: str) -> bool:
        return isinstance(signature, str) and hmac.compare_digest(
            self.sign(envelope), signature
        )

    def _event_authority_matches(
        self,
        binding_row: sqlite3.Row,
        event_row: sqlite3.Row,
        *,
        expected_binding: Binding | None = None,
        expected_mac: str | None = None,
    ) -> bool:
        try:
            envelope = json.loads(event_row["envelope_json"])
        except (TypeError, json.JSONDecodeError):
            return False
        if not isinstance(envelope, dict):
            return False
        signature = event_row["envelope_mac"]
        if expected_mac is not None and signature != expected_mac:
            return False
        if not self.verify(envelope, signature):
            return False
        if expected_binding is not None and any(
            binding_row[field] != getattr(expected_binding, field)
            for field in AUTHORITY_FIELDS
        ):
            return False
        if any(envelope.get(field) != binding_row[field] for field in AUTHORITY_FIELDS):
            return False
        if binding_row["platform"] == "desktop":
            source_message = self.reserved_source(binding_row)
            if not source_message or envelope.get("source_message_id") != source_message:
                return False
        return (
            envelope.get("source_sequence") == event_row["source_sequence"]
            and envelope.get("source_event_id") == event_row["source_event_id"]
            and envelope.get("source_turn_id") == event_row["source_turn_id"]
            and envelope.get("event_kind") == event_row["event_kind"]
        )

    def dispatch_is_eligible(
        self,
        expected_binding: Binding,
        source_sequence: int,
        expected_mac: str,
        *, completion: bool = False,
    ) -> bool:
        """Reopen durable state for the host's pre-admission authorization check."""
        try:
            with self._connect(nonblocking=True) as db:
                binding_row = db.execute(
                    "SELECT * FROM bindings WHERE binding_id=?",
                    (expected_binding.binding_id,),
                ).fetchone()
                event_row = db.execute(
                    "SELECT * FROM events WHERE binding_id=? AND source_sequence=?",
                    (expected_binding.binding_id, source_sequence),
                ).fetchone()
                return bool(
                    binding_row is not None
                    and event_row is not None
                    and binding_row["state"] == "active"
                    and event_row["status"] in ({"dispatching", "acknowledged"} if completion else {"dispatching"})
                    and self._event_authority_matches(
                        binding_row,
                        event_row,
                        expected_binding=expected_binding,
                        expected_mac=expected_mac,
                    )
                )
        except Exception:
            return False

    @staticmethod
    def _normalize_binding_values(values: Mapping[str, Any]) -> dict[str, Any]:
        normalized = {}
        for field in AUTHORITY_FIELDS:
            if field == "max_continuations":
                value = values.get(field, 1)
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 16:
                    raise ValueError("max_continuations must be between 1 and 16")
                normalized[field] = value
            elif field in {"topic_id", "sunsama_task_id"} or (values.get("platform") == "desktop" and field in {"user_id", "chat_id"}):
                normalized[field] = _clean_optional(values.get(field), field)
            else:
                maximum = (MAX_SESSION_ID_CHARS if field == "hermes_session_id" else
                           MAX_SESSION_KEY_CHARS if field == "hermes_session_key" else MAX_IDENTIFIER_CHARS)
                normalized[field] = _clean(values.get(field, "none" if field == "followup_scope" else None),
                                           field, maximum=maximum)
        if normalized["platform"] == "desktop":
            if any(normalized[field] for field in ("user_id", "chat_id", "topic_id")) or normalized["hermes_session_key"] != normalized["hermes_session_id"]:
                raise ValueError("desktop authority requires an exact physical session and no gateway route")
        return normalized

    @staticmethod
    def _insert_binding(
        db: sqlite3.Connection, values: Mapping[str, Any], now: str,
        baseline: tuple[int, str] | None = None,
    ) -> None:
        db.execute(
            """INSERT INTO bindings(
                binding_id,profile_name,t3_thread_id,t3_owner_id,t3_environment_id,
                hermes_session_key,hermes_session_id,platform,user_id,chat_id,topic_id,
                sunsama_task_id,source_identity,followup_scope,max_continuations,state,
                cursor_sequence,created_at,updated_at,binding_schema_version,baseline_captured
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?,?,?)""",
            tuple(values[field] for field in AUTHORITY_FIELDS)
            + (baseline[0] if baseline is not None else 0,
               baseline[1] if baseline is not None else now, now, SCHEMA_VERSION, baseline is not None),
        )

    def bind(self, *, baseline: tuple[int, str] | None = None, current_turn: tuple[str, str] | None = None, **values: Any) -> Binding:
        self._validate_baseline(baseline)
        normalized = self._normalize_binding_values(values)
        binding_id = normalized["binding_id"]
        now = utc_now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute(
                    "SELECT * FROM bindings WHERE binding_id=?", (binding_id,)
                ).fetchone()
                if existing is not None:
                    if current_turn is not None:
                        raise ContinuationStateError("current-turn attachment requires a fresh binding id")
                    if any(
                        existing[field] != normalized[field]
                        for field in AUTHORITY_FIELDS
                        if field != "binding_id"
                    ):
                        raise ContinuationStateError(
                            "binding exists with different authority; use a new id"
                        )
                    # Binding is idempotent, but it is not a resume operation. A
                    # stopped, cancelled, or paused mission requires the explicit
                    # operator state transition before it can observe new work.
                    db.execute(
                        "UPDATE bindings SET updated_at=? WHERE binding_id=?",
                        (now, binding_id),
                    )
                else:
                    history = db.execute(
                        "SELECT 1 FROM bindings WHERE t3_thread_id=? LIMIT 1",
                        (normalized["t3_thread_id"],),
                    ).fetchone()
                    if history is not None:
                        raise ContinuationStateError(
                            "thread has binding history; use explicit renewal"
                        )
                    self._insert_binding(db, normalized, now, baseline)
                self._insert_current_turn_reservation(db, normalized, baseline, current_turn)
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return self.get_binding(binding_id)

    def renew(self, replaces_binding_id: str, *, baseline: tuple[int, str] | None = None,
              desktop_upgrade: bool = False, current_turn: tuple[str, str] | None = None,
              replace_session_id: str | None = None, **values: Any) -> Binding:
        self._validate_baseline(baseline)
        if not isinstance(desktop_upgrade, bool):
            raise ValueError("desktop_upgrade must be a boolean")
        if any(field not in values for field in AUTHORITY_FIELDS if field != "sunsama_task_id"):
            raise ValueError("renewal requires every authority field explicitly")
        predecessor_id = _clean(replaces_binding_id, "replaces_binding_id")
        normalized = self._normalize_binding_values(values)
        if replace_session_id is not None:
            replace_session_id = _clean(replace_session_id, "replace_session_id", maximum=MAX_SESSION_ID_CHARS)
            if current_turn is None or normalized["platform"] != "desktop":
                raise ValueError("session replacement requires Desktop current-turn renewal")
        successor_id = normalized["binding_id"]
        if predecessor_id == successor_id:
            raise ContinuationStateError("renewal requires a new binding id")
        now = utc_now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                predecessor = db.execute(
                    "SELECT * FROM bindings WHERE binding_id=?", (predecessor_id,)
                ).fetchone()
                if predecessor is None:
                    raise ContinuationStateError("predecessor binding was not found")
                lineage = db.execute(
                    "SELECT successor_binding_id FROM binding_lineage "
                    "WHERE predecessor_binding_id=?",
                    (predecessor_id,),
                ).fetchone()
                successor = db.execute(
                    "SELECT * FROM bindings WHERE binding_id=?", (successor_id,)
                ).fetchone()
                if lineage is not None:
                    if current_turn is not None:
                        raise ContinuationStateError("current-turn attachment requires a fresh successor")
                    if (
                        lineage["successor_binding_id"] == successor_id
                        and successor is not None
                        and all(
                            successor[field] == normalized[field]
                            for field in AUTHORITY_FIELDS
                        )
                    ):
                        db.execute("ROLLBACK")
                        return self._effective_binding(db, successor)
                    raise ContinuationStateError(
                        "predecessor binding already has a successor"
                    )
                if successor is not None:
                    raise ContinuationStateError("successor binding id already exists")
                checked_continuity = RENEWAL_SOURCE_FIELDS + RENEWAL_DESTINATION_FIELDS
                if desktop_upgrade:
                    if predecessor["platform"] == "desktop" or normalized["platform"] != "desktop":
                        raise ContinuationStateError("desktop upgrade requires a gateway predecessor")
                    checked_continuity = ("profile_name", "t3_thread_id", "t3_owner_id", "t3_environment_id", "hermes_session_id")
                if replace_session_id is not None:
                    if (predecessor["hermes_session_id"] != replace_session_id
                            or normalized["hermes_session_id"] == replace_session_id):
                        raise ContinuationStateError("session replacement must name the exact different predecessor session")
                    checked_continuity = tuple(field for field in checked_continuity
                                               if field not in {"hermes_session_id", "hermes_session_key"})
                if any(predecessor[field] != normalized[field] for field in checked_continuity):
                    raise ContinuationStateError(
                        "renewal source and Hermes destination must match predecessor"
                    )
                if predecessor["state"] != "stopped":
                    raise ContinuationStateError(
                        "predecessor must be explicitly stopped for renewal"
                    )
                events = db.execute(
                    "SELECT * FROM events WHERE binding_id=?",
                    (predecessor_id,),
                ).fetchall()
                if len(events) != predecessor["max_continuations"]:
                    raise ContinuationStateError(
                        "predecessor continuation budget is not exhausted"
                    )
                for event in events:
                    try:
                        receipt = json.loads(event["receipt_json"])
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise ContinuationStateError(
                            "predecessor events require completed receipts"
                        ) from exc
                    if (
                        event["status"] != "acknowledged"
                        or not isinstance(receipt, dict)
                        or receipt.get("status") != "completed"
                        or not self._event_authority_matches(predecessor, event)
                    ):
                        raise ContinuationStateError(
                            "predecessor events must be acknowledged and completed"
                        )
                db.execute(
                    "UPDATE bindings SET state='stopped',updated_at=? WHERE binding_id=?",
                    (now, predecessor_id),
                )
                self._insert_binding(db, normalized, now, baseline)
                self._insert_current_turn_reservation(db, normalized, baseline, current_turn)
                db.execute(
                    """INSERT INTO binding_lineage(
                        predecessor_binding_id,successor_binding_id,renewed_at
                    ) VALUES(?,?,?)""",
                    (predecessor_id, successor_id, now),
                )
                db.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise ContinuationStateError(
                    "renewal conflicted with current binding state"
                ) from exc
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return self.get_binding(successor_id)

    @staticmethod
    def _effective_binding(db: sqlite3.Connection, row: sqlite3.Row) -> Binding:
        value = dict(row)
        value["baseline_captured"] = bool(value["baseline_captured"])
        used, pending = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(status IN ('queued','dispatching') OR "
            "(status='acknowledged' AND receipt_json IS NULL AND last_error_code IS NULL)),0) "
            "FROM events WHERE binding_id=?",
            (row["binding_id"],),
        ).fetchone()
        if value["state"] == "active" and used >= value["max_continuations"] and not pending:
            value["state"] = "exhausted"
        return Binding(**value)


    @staticmethod
    def _validate_baseline(baseline: tuple[int, str] | None) -> None:
        if baseline is None:
            return
        sequence, captured_at = baseline
        if isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 <= sequence <= 2**63 - 1:
            raise ValueError("registration baseline sequence is invalid")
        try:
            timestamp = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            if timestamp.utcoffset() is None or timestamp > datetime.now(timezone.utc):
                raise ValueError("registration timestamp is invalid")
        except (TypeError, AttributeError) as exc:
            raise ValueError("registration timestamp is invalid") from exc


    def capture_baseline(self, binding: Binding, sequence: int) -> None:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 <= sequence <= 2**63 - 1:
            raise ValueError("baseline sequence is invalid")
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE bindings SET cursor_sequence=?,baseline_captured=1,updated_at=? "
                "WHERE binding_id=? AND state='active' AND baseline_captured=0 AND cursor_sequence<=?",
                (sequence, utc_now(), binding.binding_id, sequence),
            )


    def next_queued_busy_attempts(self, binding_id: str) -> int | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT busy_attempts FROM events WHERE binding_id=? AND status='queued' "
                "ORDER BY source_sequence LIMIT 1",
                (_clean(binding_id, "binding_id"),),
            ).fetchone()
        return row["busy_attempts"] if row is not None else None


    def get_binding(self, binding_id: str) -> Binding:
        binding_id = _clean(binding_id, "binding_id")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM bindings WHERE binding_id=?", (binding_id,)
            ).fetchone()
            if row is None:
                raise ContinuationStateError("binding was not found")
            return self._effective_binding(db, row)


    def active_bindings(self, profile_name: str) -> list[Binding]:
        profile_name = _clean(profile_name, "profile_name")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM bindings WHERE profile_name=? AND state='active' ORDER BY binding_id",
                (profile_name,),
            ).fetchall()
            bindings = [self._effective_binding(db, row) for row in rows]
        return [binding for binding in bindings if binding.state == "active"]


    def set_binding_state(self, binding_id: str, state: str) -> Binding:
        if state not in {"active", "paused", "stopped", "cancelled"}:
            raise ValueError("unsupported binding state")
        binding_id = _clean(binding_id, "binding_id")
        now = utc_now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                current = db.execute("SELECT state FROM bindings WHERE binding_id=?", (binding_id,)).fetchone()
                if current is not None and current["state"] == "cancelled" and state != "cancelled":
                    raise ContinuationStateError("cancelled binding cannot be resumed or renewed")
                if state in {"active", "paused"}:
                    superseded = db.execute(
                        "SELECT 1 FROM binding_lineage WHERE predecessor_binding_id=?",
                        (binding_id,),
                    ).fetchone()
                    if superseded is not None:
                        raise ContinuationStateError(
                            "superseded binding cannot be resumed or paused"
                        )
                changed = db.execute(
                    "UPDATE bindings SET state=?,updated_at=? WHERE binding_id=?",
                    (state, now, binding_id),
                ).rowcount
                if not changed:
                    raise ContinuationStateError("binding was not found")
                if state != "active":
                    db.execute(
                        "UPDATE events SET status='cancelled',updated_at=? "
                        "WHERE binding_id=? AND status IN ('queued','dispatching')",
                        (now, binding_id),
                    )
                db.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise ContinuationStateError(
                    "thread already has an active or paused binding"
                ) from exc
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return self.get_binding(binding_id)

    def status(self, binding_id: str | None = None) -> dict[str, Any]:
        with self._connect() as db:
            if binding_id is None:
                rows = db.execute("SELECT * FROM bindings ORDER BY binding_id").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM bindings WHERE binding_id=?",
                    (_clean(binding_id, "binding_id"),),
                ).fetchall()
            result = []
            for row in rows:
                counts = db.execute(
                    "SELECT status,COUNT(*) AS count FROM events WHERE binding_id=? GROUP BY status",
                    (row["binding_id"],),
                ).fetchall()
                item = asdict(self._effective_binding(db, row))
                item["events"] = {count["status"]: count["count"] for count in counts}
                item["remaining_continuations"] = max(0, row["max_continuations"] - sum(item["events"].values()))
                item["armed"] = item["state"] == "active" and item["remaining_continuations"] > 0 and item["baseline_captured"]
                predecessor = db.execute(
                    "SELECT predecessor_binding_id FROM binding_lineage "
                    "WHERE successor_binding_id=?",
                    (row["binding_id"],),
                ).fetchone()
                successor = db.execute(
                    "SELECT successor_binding_id FROM binding_lineage "
                    "WHERE predecessor_binding_id=?",
                    (row["binding_id"],),
                ).fetchone()
                item["predecessor_binding_id"] = (
                    predecessor["predecessor_binding_id"] if predecessor else None
                )
                item["successor_binding_id"] = (
                    successor["successor_binding_id"] if successor else None
                )
                if row["platform"] == "desktop":
                    item["source_message_id"] = self.reserved_source(row)
                    item["armed"] = item["armed"] and bool(item["source_message_id"])
                result.append(item)
        return {"schema_version": SCHEMA_VERSION, "bindings": result}

    def ingest(
        self,
        binding: Binding,
        *,
        cursor_sequence: int,
        event: Mapping[str, Any] | None,
        native_event: Mapping[str, Any] | None = None,
        snapshot_gap: bool = False,
    ) -> bool:
        if isinstance(cursor_sequence, bool) or cursor_sequence < 0:
            raise ValueError("cursor_sequence is invalid")
        now = utc_now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                current = db.execute(
                    "SELECT state,cursor_sequence,max_continuations,t3_thread_id "
                    "FROM bindings WHERE binding_id=?",
                    (binding.binding_id,),
                ).fetchone()
                if current is None or current["state"] != "active":
                    db.execute("ROLLBACK")
                    return False
                if cursor_sequence <= current["cursor_sequence"]:
                    db.execute("ROLLBACK")
                    return False
                if native_event is not None and native_event.get("sequence") != cursor_sequence:
                    raise ContinuationStateError("source correlation sequence is invalid")
                if binding.platform == "desktop":
                    self._correlate_source(db, binding, native_event, snapshot_gap)
                    reservation = self._source_correlation(db, binding)
                    correlation = (reservation or {}).get("correlation", {})
                    if event is not None and correlation.get("phase") == "rejected":
                        event = None
                inserted = False
                if event is not None:
                    queued = db.execute(
                        "SELECT COUNT(*) FROM events WHERE binding_id=? AND status IN ('queued','dispatching')",
                        (binding.binding_id,),
                    ).fetchone()[0]
                    used = db.execute(
                        "SELECT COUNT(*) FROM events WHERE binding_id=?",
                        (binding.binding_id,),
                    ).fetchone()[0]
                    if queued >= self.max_queue_rows:
                        raise ContinuationStateError("continuation queue is full")
                    if used < current["max_continuations"]:
                        source_event_id = _clean(
                            event.get("source_event_id"), "source_event_id"
                        )
                        source_turn_id = _clean(
                            event.get("source_turn_id"), "source_turn_id"
                        )
                        event_kind = _clean(event.get("event_kind"), "event_kind")
                        historical_duplicate = db.execute(
                            """SELECT 1 FROM events e
                            JOIN bindings historical ON historical.binding_id=e.binding_id
                            WHERE historical.t3_thread_id=? AND e.binding_id<>?
                            AND (e.source_event_id=? OR e.source_turn_id=?) LIMIT 1""",
                            (
                                current["t3_thread_id"],
                                binding.binding_id,
                                source_event_id,
                                source_turn_id,
                            ),
                        ).fetchone()
                        if historical_duplicate is not None:
                            db.execute(
                                "UPDATE bindings SET cursor_sequence=?,updated_at=? "
                                "WHERE binding_id=?",
                                (cursor_sequence, now, binding.binding_id),
                            )
                            db.execute("COMMIT")
                            return False
                        encoded = _canonical(event)
                        if len(encoded) > MAX_EVENT_BYTES:
                            raise ContinuationStateError("normalized event exceeds its size budget")
                        signature = self.sign(event)
                        try:
                            changed = db.execute(
                                """INSERT OR IGNORE INTO events(
                                    binding_id,source_sequence,source_event_id,source_turn_id,
                                    event_kind,occurred_at,envelope_json,envelope_mac,status,
                                    attempts,created_at,updated_at
                                ) VALUES(?,?,?,?,?,?,?,?, 'queued',0,?,?)""",
                                (
                                    binding.binding_id, cursor_sequence,
                                    source_event_id,
                                    source_turn_id,
                                    event_kind,
                                    _clean(event.get("occurred_at"), "occurred_at"),
                                    encoded.decode("ascii"), signature, now, now,
                                ),
                            ).rowcount
                            inserted = bool(changed)
                        except sqlite3.IntegrityError:
                            inserted = False
                db.execute(
                    "UPDATE bindings SET cursor_sequence=?,updated_at=? WHERE binding_id=?",
                    (cursor_sequence, now, binding.binding_id),
                )
                db.execute("COMMIT")
                return inserted
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def claim_next(self, binding_id: str) -> dict[str, Any] | None:
        now = utc_now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    """SELECT e.* FROM events e JOIN bindings b USING(binding_id)
                    WHERE e.binding_id=? AND e.status='queued' AND b.state='active'
                    ORDER BY e.source_sequence LIMIT 1""",
                    (_clean(binding_id, "binding_id"),),
                ).fetchone()
                if row is None:
                    db.execute("ROLLBACK")
                    return None
                db.execute(
                    "UPDATE events SET status='dispatching',attempts=attempts+1,"
                    "receipt_json=NULL,last_error_code=NULL,updated_at=? "
                    "WHERE binding_id=? AND source_sequence=? AND status='queued'",
                    (now, binding_id, row["source_sequence"]),
                )
                db.execute("COMMIT")
                value = dict(row)
                value["attempts"] += 1
                value["receipt_json"] = None
                value["last_error_code"] = None
                return value
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise


    def finish(
        self,
        binding_id: str,
        source_sequence: int,
        status: str,
        *,
        receipt: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        refund_attempt: bool = False,
        count_busy: bool = False,
    ) -> bool:
        if status not in {
            "queued", "completed", "acknowledged", "cancelled", "blocked", "uncertain"
        }:
            raise ValueError("unsupported event state")
        receipt_json = None
        if receipt is not None:
            encoded = _canonical(receipt)
            if len(encoded) > MAX_RECEIPT_BYTES:
                raise ValueError("receipt exceeds its size budget")
            receipt_json = encoded.decode("ascii")
        if error_code is not None:
            error_code = _clean(error_code, "error_code", maximum=128)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = db.execute(
                    """UPDATE events SET status=?,receipt_json=?,last_error_code=?,
                    attempts=CASE WHEN ? AND attempts > 0 THEN attempts - 1 ELSE attempts END,
                    busy_attempts=busy_attempts+?,
                    updated_at=?
                    WHERE binding_id=? AND source_sequence=? AND status='dispatching'""",
                    (
                        status, receipt_json, error_code, bool(refund_attempt),
                        int(bool(count_busy)),
                        utc_now(), _clean(binding_id, "binding_id"), source_sequence,
                    ),
                ).rowcount
                if not changed and status in {"completed", "uncertain", "cancelled", "blocked"}:
                    # An operator can acknowledge while the host-owned Future is
                    # still running. Preserve that stronger terminal outcome while
                    # retaining the eventual host success or error receipt for audit.
                    db.execute(
                        """UPDATE events SET receipt_json=COALESCE(?,receipt_json),
                        last_error_code=?,updated_at=?
                        WHERE binding_id=? AND source_sequence=? AND status='acknowledged'""",
                        (receipt_json, error_code, utc_now(), binding_id, source_sequence),
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return bool(changed)


    def recover_dispatching(self, *, surface: str | None = None) -> int:
        """Fail closed after a crash because delivery may already have occurred."""
        suffix = ""
        if surface is not None:
            if surface not in {"desktop", "gateway"}:
                raise ValueError("unsupported consumer surface")
            operator = "=" if surface == "desktop" else "!="
            suffix = f" AND binding_id IN (SELECT binding_id FROM bindings WHERE platform {operator} 'desktop')"
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                result = db.execute(
                    "UPDATE events SET status='uncertain',last_error_code='process_restart',updated_at=? "
                    "WHERE status='dispatching'" + suffix,
                    (utc_now(),),
                ).rowcount
                # An in-run acknowledgement can precede the final host receipt.
                # Preserve its audit status while retiring the lost receipt waiter.
                result += db.execute(
                    "UPDATE events SET last_error_code='process_restart',updated_at=? "
                    "WHERE status='acknowledged' AND receipt_json IS NULL AND last_error_code IS NULL" + suffix,
                    (utc_now(),),
                ).rowcount
                db.execute("COMMIT")
                return result
            except BaseException:
                db.execute("ROLLBACK")
                raise


    def acknowledge(self, binding_id: str, source_event_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                normalized_binding_id = _clean(binding_id, "binding_id")
                normalized_event_id = _clean(source_event_id, "source_event_id")
                binding_row = db.execute(
                    "SELECT * FROM bindings WHERE binding_id=?",
                    (normalized_binding_id,),
                ).fetchone()
                event_row = db.execute(
                    "SELECT * FROM events WHERE binding_id=? AND source_event_id=?",
                    (
                        normalized_binding_id,
                        normalized_event_id,
                    ),
                ).fetchone()
                if (
                    binding_row is None
                    or event_row is None
                    or binding_row["state"] != "active"
                    or event_row["status"] not in {
                    "dispatching", "completed", "acknowledged"
                    }
                    or not self._event_authority_matches(binding_row, event_row)
                ):
                    raise ContinuationStateError(
                        "active authorized running or completed event was not found"
                    )
                if event_row["status"] != "acknowledged":
                    db.execute(
                        "UPDATE events SET status='acknowledged',updated_at=? "
                        "WHERE binding_id=? AND source_event_id=?",
                        (utc_now(), normalized_binding_id, normalized_event_id),
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @contextmanager
    def consumer_lock(self, surface: str):
        import fcntl
        if surface not in {"desktop", "gateway"}:
            raise ValueError("unsupported consumer surface")
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.data_dir / f"consumer-{surface}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            yield True
        finally:
            os.close(descriptor)

    def reserve_source(self, binding: Binding, message_id: str, *, excluded_turn_ids=()) -> None:
        message_id = _clean(message_id, "message_id", maximum=256)
        envelope = {field: getattr(binding, field) for field in AUTHORITY_FIELDS}
        envelope["source_message_id"] = message_id
        envelope["correlation"] = {"phase": "awaiting_start", "turn_id": None,
            "excluded_turn_ids": sorted({_clean(turn, "excluded_turn_id") for turn in excluded_turn_ids if turn})}
        encoded, signature = _canonical(envelope).decode("ascii"), self.sign(envelope)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT envelope_json,envelope_mac FROM source_reservations WHERE binding_id=?", (binding.binding_id,)).fetchone()
            if existing is not None and tuple(existing) != (encoded, signature):
                raise ContinuationStateError("binding already reserves a different source message")
            db.execute("INSERT OR IGNORE INTO source_reservations VALUES(?,?,?)", (binding.binding_id, encoded, signature))
            db.execute("COMMIT")

    def reserved_source(self, binding: Binding | Mapping[str, Any]) -> str | None:
        values = asdict(binding) if isinstance(binding, Binding) else dict(binding)
        with self._connect() as db:
            row = db.execute("SELECT envelope_json,envelope_mac FROM source_reservations WHERE binding_id=?", (values["binding_id"],)).fetchone()
        if row is None:
            return None
        envelope = json.loads(row[0])
        if not self.verify(envelope, row[1]) or any(envelope.get(field) != values[field] for field in AUTHORITY_FIELDS):
            raise ContinuationStateError("source reservation authority is invalid")
        return _clean(envelope.get("source_message_id"), "source_message_id", maximum=256)

    def _source_correlation(self, db, binding):
        row = db.execute("SELECT envelope_json,envelope_mac FROM source_reservations WHERE binding_id=?",
                         (binding.binding_id,)).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        if not self.verify(value, row[1]) or any(value.get(field) != getattr(binding, field) for field in AUTHORITY_FIELDS):
            raise ContinuationStateError("source reservation authority is invalid")
        return value

    def reserved_turn(self, binding):
        with self._connect() as db:
            value = self._source_correlation(db, binding)
        correlation = (value or {}).get("correlation", {})
        return correlation.get("turn_id") if correlation.get("phase") == "mapped" else None

    def _correlate_source(self, db, binding, native_event, snapshot_gap):
        """Correlate the visible native turn-start user message with its running turn."""
        value = self._source_correlation(db, binding)
        if value is None or "correlation" not in value:
            return
        correlation = value["correlation"]
        phase = correlation["phase"]
        if phase == "rejected":
            return
        if snapshot_gap:
            correlation["phase"] = "rejected"
        elif native_event is not None:
            payload = native_event.get("payload", {})
            if (native_event.get("aggregateKind") != "thread"
                    or native_event.get("aggregateId") != binding.t3_thread_id
                    or not isinstance(payload, dict) or payload.get("threadId") != binding.t3_thread_id):
                raise ContinuationStateError("source correlation event identity is invalid")
            try:
                occurred_at = datetime.fromisoformat(native_event["occurredAt"].replace("Z", "+00:00"))
                created_at = datetime.fromisoformat(binding.created_at.replace("Z", "+00:00"))
                if occurred_at.utcoffset() is None or occurred_at < created_at:
                    raise ValueError("event predates reservation")
                _clean(native_event.get("eventId"), "source_event_id")
            except (KeyError, AttributeError, TypeError, ValueError) as exc:
                raise ContinuationStateError("source correlation event chronology is invalid") from exc
            kind = native_event.get("type")
            # subscribeThread filters out turn-start-requested. The native turn.start
            # command emits this user message atomically with that request; imported
            # history is the other user-message emitter and is explicitly marked.
            if kind == "thread.message-sent" and payload.get("role") == "user":
                metadata = native_event.get("metadata")
                command_id = native_event.get("commandId")
                witness = (payload.get("messageId") == value["source_message_id"]
                    and "turnId" in payload and payload["turnId"] is None
                    and payload.get("streaming") is False
                    and isinstance(command_id, str) and bool(command_id.strip())
                    and isinstance(metadata, dict) and metadata.get("historyImport", False) is False)
                correlation["phase"] = "pending" if phase == "awaiting_start" and witness else "rejected"
                if correlation["phase"] == "pending":
                    correlation.update(start_event_id=native_event["eventId"], start_sequence=native_event["sequence"],
                        start_command_id=_clean(command_id, "source_command_id"))
            elif kind == "thread.session-set":
                session = payload.get("session") or {}
                status, turn = session.get("status"), session.get("activeTurnId")
                if status == "running":
                    if phase == "pending" and isinstance(turn, str) and turn and turn not in correlation["excluded_turn_ids"]:
                        correlation.update(phase="mapped", turn_id=_clean(turn, "source_turn_id"),
                            mapped_event_id=native_event["eventId"], mapped_sequence=native_event["sequence"])
                    elif phase != "mapped" or turn != correlation["turn_id"]:
                        correlation["phase"] = "rejected"
                elif phase != "mapped" and status in {"ready", "error", "stopped", "interrupted"}:
                    correlation["phase"] = "rejected"
            elif kind in {"thread.checkpoint-revert-requested", "thread.reverted", "thread.deleted", "thread.session-stop-requested", "thread.turn-interrupt-requested"}:
                correlation["phase"] = "rejected"
            elif kind == "thread.activity-appended" and phase != "mapped":
                activity = payload.get("activity") or {}
                if activity.get("kind") in {"provider.turn.start.failed", "context-compaction"}:
                    correlation["phase"] = "rejected"
        db.execute("UPDATE source_reservations SET envelope_json=?,envelope_mac=? WHERE binding_id=?",
                   (_canonical(value).decode("ascii"), self.sign(value), binding.binding_id))

    def _insert_current_turn_reservation(self, db, values, baseline, current_turn) -> None:
        if current_turn is None:
            return
        if values["platform"] != "desktop" or baseline is None or len(current_turn) != 2:
            raise ValueError("current-turn attachment requires Desktop and a captured baseline")
        message_id, turn_id = current_turn
        envelope = {field: values[field] for field in AUTHORITY_FIELDS}
        envelope["source_message_id"] = _clean(message_id, "source_message_id", maximum=256)
        envelope["correlation"] = {
            "phase": "mapped", "turn_id": _clean(turn_id, "source_turn_id"), "excluded_turn_ids": [],
            "registration_sequence": baseline[0], "registration_created_at": baseline[1],
        }
        db.execute("INSERT INTO source_reservations VALUES(?,?,?)",
            (values["binding_id"], _canonical(envelope).decode("ascii"), self.sign(envelope)))

    @staticmethod
    def _create_extensions(db: sqlite3.Connection) -> None:
        db.execute("CREATE TABLE source_reservations (binding_id TEXT PRIMARY KEY REFERENCES bindings(binding_id), envelope_json TEXT NOT NULL, envelope_mac TEXT NOT NULL)")
        db.execute("CREATE TABLE notification_targets (binding_id TEXT PRIMARY KEY REFERENCES bindings(binding_id), envelope_json TEXT NOT NULL, envelope_mac TEXT NOT NULL)")
        db.execute("CREATE TABLE notifications (binding_id TEXT NOT NULL REFERENCES bindings(binding_id), source_sequence INTEGER NOT NULL, envelope_json TEXT NOT NULL, envelope_mac TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0, receipt_json TEXT, PRIMARY KEY(binding_id,source_sequence))")
        db.execute("INSERT INTO meta(key,value) VALUES('source_reservation_version','1')")

    def _validate_extensions(self, db: sqlite3.Connection) -> None:
        expected = {
            "source_reservations": ("binding_id", "envelope_json", "envelope_mac"),
            "notification_targets": ("binding_id", "envelope_json", "envelope_mac"),
            "notifications": ("binding_id", "source_sequence", "envelope_json", "envelope_mac", "status", "attempts", "retry_at", "receipt_json"),
        }
        version = db.execute("SELECT value FROM meta WHERE key='source_reservation_version'").fetchone()
        if version is None or version[0] != '1':
            raise ContinuationStateError("unsupported source reservation extension")
        for table, columns in expected.items():
            keys = {("binding_id", "source_sequence")} if table == "notifications" else {("binding_id",)}
            foreign_keys = db.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            if (self._table_columns(db, table) != columns or self._unique_index_columns(db, table) != keys
                    or len(foreign_keys) != 1 or foreign_keys[0]["table"] != "bindings"
                    or foreign_keys[0]["from"] != "binding_id" or foreign_keys[0]["to"] != "binding_id"):
                raise ContinuationStateError("continuation extension schema is not canonical")

    def _migrate_v3_to_current(self, db: sqlite3.Connection) -> None:
        self._validate_schema(db, version=3)
        db.execute("PRAGMA foreign_keys=OFF")
        if db.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
            raise ContinuationStateError("continuation migration could not suspend foreign keys")
        try:
            db.execute("BEGIN IMMEDIATE")
            self._create_bindings_table(db, "bindings_v3")
            db.execute(
                f"INSERT INTO bindings_v3({','.join(BINDING_COLUMNS)}) "
                f"SELECT {','.join(V1_BINDING_COLUMNS)},?,baseline_captured FROM bindings",
                (SCHEMA_VERSION,),
            )
            db.execute("DROP TABLE bindings")
            db.execute("ALTER TABLE bindings_v3 RENAME TO bindings")
            db.execute("CREATE UNIQUE INDEX bindings_live_thread_idx ON bindings(t3_thread_id) WHERE state IN ('active','paused')")
            db.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            self._create_extensions(db)
            self._validate_schema(db)
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.execute("PRAGMA foreign_keys=ON")
