"""Optional mission ledger must not gate bounded continuation authority."""
import argparse
import tempfile
import unittest
from types import SimpleNamespace

import continuation
import continuation_cli
import continuation_handoff
from continuation_state import ContinuationStore
from tests.test_continuation_state import binding_values, envelope
from tests.support import detail_snapshot, latest_turn, session


class OptionalLedgerTests(unittest.TestCase):
    def test_missing_ledger_persists_signed_empty_value_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ContinuationStore(directory)
            store.initialize()
            values = binding_values()
            values.pop('sunsama_task_id')
            binding = store.bind(baseline=(0, '2026-09-12T00:00:00Z'), **values)
            self.assertEqual(binding.sunsama_task_id, '')
            event = envelope(binding)
            self.assertTrue(store.ingest(binding, cursor_sequence=1, event=event))
            store = ContinuationStore(directory)
            claimed = store.claim_next(binding.binding_id)
            self.assertTrue(store.dispatch_is_eligible(binding, 1, claimed['envelope_mac']))
            self.assertIn('No external mission ledger is bound', continuation.trusted_pm_content(binding, event, 'receipt-1'))
            store.acknowledge(binding.binding_id, event['source_event_id'])
            store.finish(binding.binding_id, 1, 'completed', receipt={'status': 'completed', 'receipt_id': 'receipt-1'})
            store.set_binding_state(binding.binding_id, 'stopped')
            # Renewal requires explicit finite authority but may omit the optional ledger.
            values['binding_id'] = 'mission-2'
            renewed = store.renew(binding.binding_id, baseline=(2, '2026-09-12T00:00:01Z'), **values)
            self.assertEqual(renewed.sunsama_task_id, '')

    def test_required_handoff_without_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            target = {'profile_name': 'default', 'session_id': 'session-1'}
            ctx = SimpleNamespace(state=SimpleNamespace(data_dir=directory),
                current_desktop_destination=lambda: target,
                desktop_continuation_readiness=lambda destination: {'ready': True},
                get_config=lambda key, default=None: True if key == "continuation_enabled" else default)
            args = dict(binding_id='mission-1', owner_id='owner-1', environment_id='environment-1',
                source_identity='source-1', followup_scope='none', max_continuations=1, expected_turn_id='turn-old')
            before = detail_snapshot(sequence=3, turn=latest_turn(turn_id='turn-old', state='completed'), current_session=session(status='ready'))
            transport = SimpleNamespace(get_environment_descriptor=lambda: {'environmentId': 'environment-1'})
            result = continuation_handoff.prepare_handoff(ctx, args, 'thread-1', before, transport, 'message-1')
            self.assertTrue(result['armed'])
            self.assertEqual(ContinuationStore(directory).get_binding('mission-1').sunsama_task_id, '')

    def test_cli_does_not_require_sunsama_argument(self):
        parser = argparse.ArgumentParser()
        continuation_cli.setup_continuation_cli(parser)
        args = parser.parse_args(['bind', '--binding-id', 'mission-1', '--thread-id', 'thread-1',
            '--owner-id', 'owner-1', '--environment-id', 'environment-1', '--session-key', 'session-1',
            '--session-id', 'session-1', '--platform', 'desktop', '--user-id', '', '--chat-id', '',
            '--source-identity', 'fixture'])
        self.assertEqual(args.sunsama_task_id, '')

    def test_present_ledger_stays_signed_and_invalid_ids_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ContinuationStore(directory)
            store.initialize()
            binding = store.bind(**binding_values())
            self.assertEqual(binding.sunsama_task_id, 'task-1')
            for bad in (' ', 'bad\nvalue', 42, False):
                with self.subTest(value=bad), self.assertRaises(ValueError):
                    store._normalize_binding_values(binding_values(sunsama_task_id=bad))

    def test_excluded_required_handoff_refuses_before_binding_or_source_read(self):
        from client import ConflictError
        with tempfile.TemporaryDirectory() as directory:
            ctx=SimpleNamespace(state=SimpleNamespace(data_dir=directory),
                get_config=lambda key,default=None:['mission-1'] if key=='continuation_excluded_bindings' else default)
            value=dict(binding_id='mission-1',owner_id='owner-1',environment_id='environment-1',
                source_identity='source-1',followup_scope='none',max_continuations=1,expected_turn_id=None)
            with self.assertRaisesRegex(ConflictError,'excluded'):
                continuation_handoff.prepare_handoff(ctx,value,'thread-1',None,None,'message-1')
            from pathlib import Path
            self.assertFalse((Path(directory)/'continuation.sqlite3').exists())
