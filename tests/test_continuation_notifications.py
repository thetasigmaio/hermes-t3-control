import asyncio
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import continuation_notifications as notices
from continuation_state import ContinuationStore
from tests.test_continuation_state import binding_values, envelope

TARGET = dict(profile_name='default',platform='telegram',chat_id='123',topic_id='456')

class NotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=ContinuationStore(self.temp.name);self.store.initialize()
        self.binding=self.store.bind(baseline=(0,'2026-09-12T00:00:00Z'), **binding_values(
            platform='desktop',hermes_session_key='session-1',user_id='',chat_id='',topic_id='',sunsama_task_id=''))
        self.sent=[]
        async def sender(content, *, destination, eligibility_check):
            self.assertTrue(eligibility_check());self.sent.append((content,destination))
            return dict(status='delivered',message_id='42',destination=destination)
        self.ctx=SimpleNamespace(send_plugin_notification=sender,telegram_notification_target=lambda:TARGET,get_config=lambda key,default=None:default)
        notices.register_target(self.store,self.binding,TARGET)
        self.store.reserve_source(self.binding,"source-message-1")
        self.event={**envelope(self.binding), "source_message_id":"source-message-1"}
        self.store.ingest(self.binding,cursor_sequence=1,event=self.event)
        self.store.claim_next(self.binding.binding_id)

    def complete(self):
        self.store.acknowledge(self.binding.binding_id,self.event['source_event_id'])
        self.store.finish(self.binding.binding_id,1,'completed',receipt={'status':'completed','receipt_id':'pm-receipt'})

    async def test_ack_and_transport_receipt_required_then_single_notice_across_restart(self):
        await notices.drain(self.ctx,self.store);self.assertEqual(self.sent,[])
        self.store.acknowledge(self.binding.binding_id,self.event['source_event_id'])
        await notices.drain(self.ctx,self.store);self.assertEqual(self.sent,[])
        self.complete()
        await notices.drain(self.ctx,self.store)
        notices.recover(ContinuationStore(self.temp.name))
        await notices.drain(self.ctx,self.store)
        self.assertEqual(len(self.sent),1)
        self.assertEqual(json.loads(notices.status(self.store)[0]['receipt_json'])['message_id'],'42')

    async def test_uncertain_send_never_replays_pm_or_notice(self):
        self.complete()
        async def timeout(*args,**kwargs):self.sent.append('attempt');raise TimeoutError()
        self.ctx.send_plugin_notification=timeout
        await notices.drain(self.ctx,self.store);notices.recover(self.store)
        await notices.drain(self.ctx,self.store)
        self.assertEqual(self.sent,['attempt'])
        self.assertEqual(notices.status(self.store)[0]['status'],'uncertain')
        self.assertEqual(self.store.status()['bindings'][0]['events'],{'acknowledged':1})

    async def test_cancel_and_wrong_target_prevent_delivery(self):
        self.complete();self.store.set_binding_state(self.binding.binding_id,'cancelled')
        await notices.drain(self.ctx,self.store);self.assertEqual(self.sent,[])
        with self.assertRaises(Exception):notices.register_target(self.store,self.binding,{**TARGET,'chat_id':'999'})

    async def test_explicit_rate_limit_is_bounded_and_restart_safe(self):
        self.complete()
        async def rate_limit(*args,**kwargs):self.sent.append('attempt');return dict(status='retryable',retry_after=1)
        self.ctx.send_plugin_notification=rate_limit
        for timestamp in (100,102,104,106):
            with patch.object(notices.time,'time',return_value=timestamp):await notices.drain(self.ctx,self.store)
        self.assertEqual(len(self.sent),3)
        self.assertEqual(notices.status(self.store)[0]['status'],'blocked')

    async def test_inflight_crash_is_uncertain_and_no_old_bindings_auto_opt_in(self):
        self.complete();notices._enqueue_completed(self.store)
        with self.store._connect() as db:
            db.execute("UPDATE notifications SET status='sending'")
        notices.recover(self.store);await notices.drain(self.ctx,self.store)
        self.assertEqual(self.sent,[])
        self.assertEqual(notices.status(self.store)[0]['status'],'uncertain')
        values=binding_values(binding_id='other',t3_thread_id='other',sunsama_task_id='')
        other=self.store.bind(**values)
        self.assertIsNone(notices.resolve_requested_target(self.ctx,False))
        with self.store._connect() as db:
            self.assertIsNone(db.execute('SELECT * FROM notification_targets WHERE binding_id=?',(other.binding_id,)).fetchone())

    async def test_old_stopped_notices_do_not_starve_later_verified_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            store=ContinuationStore(directory);store.initialize()
            for i in range(129):
                binding=store.bind(baseline=(0,'2026-09-12T00:00:00Z'),**binding_values(
                    binding_id=f'mission-{i}',t3_thread_id=f'thread-{i}',platform='desktop',
                    hermes_session_key='session-1',user_id='',chat_id='',topic_id='',sunsama_task_id=''))
                notices.register_target(store,binding,TARGET)
                store.reserve_source(binding,f'message-{i}')
                event={**envelope(binding), 'source_message_id':f'message-{i}'}
                store.ingest(binding,cursor_sequence=1,event=event);store.claim_next(binding.binding_id)
                store.acknowledge(binding.binding_id,event['source_event_id'])
                store.finish(binding.binding_id,1,'completed',receipt={'status':'completed','receipt_id':'pm-receipt'})
                if i<128:store.set_binding_state(binding.binding_id,'stopped')
            await notices.drain(self.ctx,store)
            self.assertEqual(len(self.sent),1)
            self.assertEqual(notices.status(store)[0]['binding_id'],'mission-128')

    async def test_exclusion_prevents_notice_even_after_primary_verification(self):
        self.complete()
        self.ctx.get_config=lambda key,default=None:[self.binding.binding_id] if key=='continuation_excluded_bindings' else default
        await notices.drain(self.ctx,self.store)
        self.assertEqual(self.sent,[])
        self.assertEqual(notices.status(self.store)[0]['status'],'cancelled')
