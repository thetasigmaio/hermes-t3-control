"""Durable opt-in notification leg, independent of the completed PM turn."""
from __future__ import annotations

import asyncio
import json
import time

try:
    from .continuation_state import AUTHORITY_FIELDS, ContinuationStateError
except ImportError:
    from continuation_state import AUTHORITY_FIELDS, ContinuationStateError


def initialize(store):
    with store._connect() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS notification_targets (
                binding_id TEXT PRIMARY KEY REFERENCES bindings(binding_id),
                envelope_json TEXT NOT NULL, envelope_mac TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notifications (
                binding_id TEXT NOT NULL REFERENCES bindings(binding_id),
                source_sequence INTEGER NOT NULL, envelope_json TEXT NOT NULL, envelope_mac TEXT NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                retry_at REAL NOT NULL DEFAULT 0, receipt_json TEXT,
                PRIMARY KEY(binding_id,source_sequence));
        ''')


def resolve_requested_target(ctx, requested):
    if not isinstance(requested, bool):
        raise ValueError('notify_telegram must be a boolean')
    if not requested:
        return None
    resolver = getattr(ctx, 'telegram_notification_target', None)
    sender = getattr(ctx, 'send_plugin_notification', None)
    target = resolver() if callable(resolver) and callable(sender) else None
    if not isinstance(target, dict) or set(target) != {'profile_name','platform','chat_id','topic_id'}:
        raise ValueError('no supported configured Telegram notification target')
    if target['profile_name'] != 'default' or target['platform'] != 'telegram':
        raise ValueError('unsupported Telegram notification target')
    if not isinstance(target['chat_id'], str) or not target['chat_id'].lstrip('-').isdigit():
        raise ValueError('invalid Telegram chat')
    if not isinstance(target['topic_id'], str) or (target['topic_id'] and not target['topic_id'].isdigit()):
        raise ValueError('invalid Telegram topic')
    return target


def register_target(store, binding, target):
    if target is None:
        return
    if binding.platform != 'desktop':
        raise ValueError('additional Telegram notice requires a Desktop primary destination')
    initialize(store)
    value = {'authority': {f: getattr(binding, f) for f in AUTHORITY_FIELDS}, 'destination': target}
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'))
    with store._connect() as db:
        existing = db.execute('SELECT envelope_json,envelope_mac FROM notification_targets WHERE binding_id=?', (binding.binding_id,)).fetchone()
        if existing and (existing[0] != encoded or not store.verify(value, existing[1])):
            raise ContinuationStateError('notification authority is immutable')
        db.execute('INSERT OR IGNORE INTO notification_targets VALUES(?,?,?)', (binding.binding_id, encoded, store.sign(value)))


def _authority(store, db, binding_id):
    target = db.execute('SELECT * FROM notification_targets WHERE binding_id=?', (binding_id,)).fetchone()
    binding = db.execute('SELECT * FROM bindings WHERE binding_id=?', (binding_id,)).fetchone()
    if not target or not binding or binding['platform'] != 'desktop' or binding['state'] != 'active':
        return None
    value = json.loads(target['envelope_json'])
    if not store.verify(value, target['envelope_mac']) or value['authority'] != {f: binding[f] for f in AUTHORITY_FIELDS}:
        return None
    return value


def recover(store):
    initialize(store)
    with store._connect() as db:
        db.execute("UPDATE notifications SET status='uncertain' WHERE status='sending'")


def _enqueue_completed(store):
    with store._lock, store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            rows = db.execute('''SELECT e.* FROM events e JOIN notification_targets t USING(binding_id)
                JOIN bindings b USING(binding_id)
                LEFT JOIN notifications n ON n.binding_id=e.binding_id AND n.source_sequence=e.source_sequence
                WHERE e.status='acknowledged' AND b.state='active' AND n.binding_id IS NULL
                AND json_valid(e.receipt_json) AND json_extract(e.receipt_json,'$.status')='completed' LIMIT 128''').fetchall()
            for event in rows:
                receipt = json.loads(event['receipt_json'] or '{}')
                target = _authority(store, db, event['binding_id'])
                source = json.loads(event['envelope_json'])
                if (not target or not store.verify(source, event['envelope_mac'])
                    or any(source.get(f) != target['authority'][f] for f in AUTHORITY_FIELDS)):
                    rejected = {'reason': 'invalid_notification_authority'}
                    db.execute('INSERT OR IGNORE INTO notifications(binding_id,source_sequence,envelope_json,envelope_mac,status) VALUES(?,?,?,?,?)',
                        (event['binding_id'],event['source_sequence'],json.dumps(rejected),store.sign(rejected),'blocked'))
                    continue
                value = {'target': target, 'event_mac': event['envelope_mac'],
                         'source_event_id': event['source_event_id'], 'source_turn_id': event['source_turn_id'],
                         'receipt_id': receipt.get('receipt_id', '')}
                db.execute('INSERT OR IGNORE INTO notifications(binding_id,source_sequence,envelope_json,envelope_mac,status) VALUES(?,?,?,?,?)',
                    (event['binding_id'], event['source_sequence'], json.dumps(value,sort_keys=True,separators=(',',':')), store.sign(value), 'queued'))
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK')
            raise


def _eligible(store, binding_id, sequence, value):
    with store._connect(nonblocking=True) as db:
        if _authority(store, db, binding_id) != value['target']:
            return False
        event = db.execute('SELECT * FROM events WHERE binding_id=? AND source_sequence=?', (binding_id,sequence)).fetchone()
        return bool(event and event['status'] == 'acknowledged' and event['envelope_mac'] == value['event_mac']
                    and store.verify(json.loads(event['envelope_json']), event['envelope_mac'])
                    and json.loads(event['receipt_json'] or '{}').get('status') == 'completed')


async def drain(ctx, store):
    _enqueue_completed(store)
    with store._lock, store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            row = db.execute("SELECT * FROM notifications WHERE status='queued' AND retry_at<=? ORDER BY source_sequence LIMIT 1", (time.time(),)).fetchone()
            if row:
                db.execute("UPDATE notifications SET status='sending',attempts=attempts+1 WHERE binding_id=? AND source_sequence=?", (row['binding_id'],row['source_sequence']))
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK');raise
    if not row:
        return
    value = json.loads(row['envelope_json'])
    def eligible():
        try:
            try:
                from .continuation import binding_enabled
            except ImportError:
                from continuation import binding_enabled
            if not binding_enabled(ctx, row["binding_id"]):
                return False
            return store.verify(value, row['envelope_mac']) and _eligible(store,row['binding_id'],row['source_sequence'],value)
        except Exception:
            return False
    receipt = {'status': 'cancelled'}
    if eligible():
        sender = getattr(ctx, 'send_plugin_notification', None)
        receipt = {'status': 'unsupported'}
        if callable(sender):
            # Provider output is never copied into the notification or used as new authority.
            content = ('T3: Hermes verified a task result in the original conversation.\n'
                       f"Binding: {row['binding_id']}\nEvent: {value['source_event_id']}\nTurn: {value['source_turn_id']}\nReceipt: {value['receipt_id']}")
            try:
                receipt = await asyncio.wait_for(sender(content, destination=value['target']['destination'], eligibility_check=eligible), 45)
            except asyncio.CancelledError:
                raise  # durable 'sending' is recovered as uncertain, never sent twice
            except Exception:
                receipt = {'status': 'uncertain'}
    status = receipt.get('status') if isinstance(receipt, dict) else 'uncertain'
    retry_at = 0
    if status == 'delivered':
        if receipt.get('destination') != value['target']['destination'] or not str(receipt.get('message_id','')).isdigit():
            status = 'uncertain'
    elif status == 'retryable' and row['attempts'] < 2:
        status = 'queued'
        retry_at = time.time() + min(300, max(1, float(receipt.get('retry_after', 1))))
    elif status not in {'cancelled','uncertain'}:
        status = 'blocked'
    with store._connect() as db:
        # Keep only bounded receipt fields, never transport exception strings.
        clean = {'status': status}
        if status == 'delivered':
            clean.update(message_id=str(receipt['message_id']), destination=value['target']['destination'])
        db.execute('UPDATE notifications SET status=?,retry_at=?,receipt_json=? WHERE binding_id=? AND source_sequence=?',
                   (status,retry_at,json.dumps(clean,sort_keys=True),row['binding_id'],row['source_sequence']))


def status(store, binding_id=None):
    with store._connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notifications'").fetchone():
            return []
        sql='SELECT binding_id,source_sequence,status,attempts,receipt_json FROM notifications'
        rows=db.execute(sql+(' WHERE binding_id=?' if binding_id else ''),(binding_id,) if binding_id else ()).fetchall()
        return [dict(row) for row in rows]
