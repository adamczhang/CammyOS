"""Finite live-turn behaviour with fake media/providers; no camera/models/audio."""
import importlib.util
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'host'),str(ROOT/'sdk')]
from cammy_conversation import Conversation,ProcessJob


class Clock:
    def __init__(self):self.now=100
    def __call__(self):return self.now


class Job:
    def __init__(self):self.result=None;self.cancelled=False
    def poll(self):return self.result
    def cancel(self):self.cancelled=True # deliberately ignores cancellation
    def finish(self,value=b'',error=None):self.result=(value,error)


class Speech:
    def __init__(self):self.calls=[];self.jobs=[]
    def ready(self,speaker):self.calls.append(('ready',speaker))
    def job(self,kind,value):
        self.calls.append((kind,value));job=Job();self.jobs.append(job);return job
    def transcribe(self,pcm):return self.job('stt',len(pcm))
    def synthesize(self,text):return self.job('tts',text)
    def play(self,pcm):return self.job('play',len(pcm))


class Live:
    def __init__(self):
        self.phase='idle';self.session=None;self.boot='boot-a';self.sequence=0;self.pcm=b'';self.calls=[];self.gap=False
    def status(self):return {'phase':self.phase,'session':self.session,'boot_id':self.boot,'audio_sequence':self.sequence,'synthetic':True,
        'frames':3,'frame_age_seconds':0,'audio_age_seconds':0}
    def start(self,seconds):
        if self.phase!='idle':raise RuntimeError('busy')
        self.calls.append(('start',seconds));self.session='session-'+str(len(self.calls));self.phase='live';return self.status()
    def stop(self,session):
        if session!=self.session:raise AssertionError('stopped another owner')
        self.calls.append(('stop',session));self.phase='idle'
    def audio_tail(self,after):
        return (self.pcm if self.sequence>after else b''),dict(self.status(),latest_sequence=self.sequence,history_gap=self.gap)
    def feed(self,pcm=b'\x11\x01'*16000):self.sequence+=1;self.pcm=pcm


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.live=Live();self.speech=Speech();self.faces=[]
        self.c=Conversation(self.live,self.live.start,self.live.stop,self.faces.append,self.speech,self.clock,False)
    def ready(self,speaker=False):
        value=self.c.start(60,speaker);self.c.step();return value['session']
    def recognize(self,speaker=False,text='Hello from the microphone.'):
        session=self.ready(speaker);self.c.listen(session);self.live.feed();self.c.step();self.c.finish(session)
        self.speech.jobs[-1].finish(json.dumps({'text':text}).encode());self.c.step();return session,self.c.turn

    def test_construction_and_idle_do_not_start_media_or_models(self):
        self.c.step();self.assertEqual(self.live.calls,[]);self.assertEqual(self.speech.calls,[])

    def test_transport_cleanup_blocks_new_session_and_is_reported(self):
        self.c.cleanup_ready=lambda:False
        self.assertTrue(self.c.status()['media_cleanup_pending'])
        with self.assertRaises(RuntimeError):self.c.start()
        self.assertEqual(self.live.calls,[]);self.assertEqual(self.speech.calls,[])

    def test_ready_requires_both_first_video_and_audio_and_staleness_stops(self):
        session=self.c.start()['session'];original=self.live.status
        self.live.status=lambda:dict(original(),audio_age_seconds=None)
        self.c.step();self.assertEqual(self.c.phase,'starting')
        with self.assertRaises(RuntimeError):self.c.listen(session)
        self.live.status=original;self.c.step();self.assertEqual(self.c.phase,'ready')
        self.live.status=lambda:dict(original(),frame_age_seconds=3)
        self.c.step();self.assertEqual(self.c.phase,'idle');self.assertEqual(self.c.reason,'media-stale')

    def test_preparation_can_take_longer_than_conversation_without_losing_session(self):
        self.c.start(5);self.live.phase='preparing'
        self.clock.now+=60;self.c.step()
        self.assertEqual(self.c.phase,'starting')
        self.live.phase='live';self.c.step()
        self.assertEqual(self.c.phase,'ready');self.assertEqual(self.c.ends,self.clock.now+5)
        self.clock.now+=5;self.c.step();self.assertEqual(self.c.phase,'idle')

    def test_full_turn_reply_uses_explicit_p15_route_and_completes(self):
        session,turn=self.recognize(True)
        inbox=self.c.status()['inbox'];self.assertTrue(inbox['synthetic']);self.assertEqual(inbox['boot_id'],'boot-a')
        self.c.reply(session,turn,'I heard your test.')
        self.assertEqual(self.c.phase,'synthesizing')
        self.speech.jobs[-1].finish(b'\x12\x01'*24000);self.c.step();self.assertEqual(self.c.phase,'playing')
        self.speech.jobs[-1].finish();self.c.step();self.assertEqual(self.c.phase,'ready')
        self.assertIn(('play',48000),self.speech.calls)
        self.c.stop();self.assertEqual(self.live.phase,'idle');self.assertIsNone(self.c.inbox)

    def test_speaker_disabled_is_text_only_and_never_synthesizes(self):
        session,turn=self.recognize();self.c.reply(session,turn,'Text-only answer')
        self.assertEqual(self.c.phase,'ready');self.assertFalse(any(k in ('tts','play') for k,_ in self.speech.calls))

    def test_audio_before_press_is_not_submitted(self):
        session=self.ready();self.live.feed();self.c.listen(session);self.c.step();self.c.finish(session)
        self.assertEqual(self.c.phase,'ready');self.assertFalse(self.speech.jobs)

    def test_short_empty_and_oversized_utterances(self):
        session=self.ready();self.c.listen(session);self.live.feed(b'\x01\x00'*100);self.c.finish(session)
        self.assertEqual(self.c.phase,'ready')
        self.c.listen(session);self.live.feed(b'\x01\x00'*128001);self.c.step()
        self.assertEqual(self.c.phase,'idle');self.assertFalse(self.speech.jobs)

    def test_stop_rejects_late_recognition_and_blocks_new_until_reaped(self):
        session=self.ready();self.c.listen(session);self.live.feed();self.c.finish(session);old=self.c.job
        self.c.stop();old.finish(b'{"text":"late words"}')
        self.c.step();self.assertIsNone(self.c.inbox);self.assertEqual(self.c.phase,'idle')
        self.assertTrue(old.cancelled);self.assertEqual(self.c.status()['capture_bytes'],0)

    def test_pending_worker_prevents_reacquisition(self):
        session,turn=self.recognize(True);self.c.reply(session,turn,'Answer');old=self.c.job;self.c.stop()
        with self.assertRaises(RuntimeError):self.c.start()
        old.finish(b'late pcm');self.c.step();self.c.start();self.assertEqual(self.c.phase,'starting')
        self.assertFalse(any(k=='play' for k,_ in self.speech.calls))

    def test_stop_during_playback_cancels_owned_player(self):
        session,turn=self.recognize(True);self.c.reply(session,turn,'Answer');self.c.job.finish(b'\x01\x00'*100)
        self.c.step();player=self.c.job;self.c.stop();self.assertTrue(player.cancelled)
        player.finish();self.c.step();self.assertEqual(self.c.phase,'idle')

    def test_session_replacement_stops_work_without_stopping_other_media(self):
        session=self.ready();self.c.listen(session);self.live.session='other';before=list(self.live.calls)
        self.c.step();self.assertEqual(self.c.phase,'idle');self.assertEqual(self.live.calls,before)

    def test_stop_binding_race_still_invalidates_jobs_without_global_fallback(self):
        session=self.ready();self.c.listen(session);self.live.feed();self.c.finish(session);job=self.c.job
        def raced_stop(session):raise RuntimeError('replacement raced the status read')
        self.c.stop_media=raced_stop
        self.c.stop();self.assertEqual(self.c.phase,'idle');self.assertTrue(job.cancelled)
        self.assertEqual(self.c.reason,'media-session-changed-or-stop-unconfirmed')
        self.assertIsNone(self.c.inbox);self.assertEqual(self.c.buffer,b'')

    def test_boot_change_audio_gap_and_external_stop_invalidate(self):
        for fault in ('boot','gap','stop'):
            with self.subTest(fault=fault):
                self.setUp();session=self.ready();self.c.listen(session);self.live.feed()
                if fault=='boot':self.live.boot='boot-b'
                elif fault=='gap':self.live.gap=True
                else:self.live.phase='idle'
                self.c.step();self.assertEqual(self.c.phase,'idle');self.assertIsNone(self.c.inbox)

    def test_expiry_stops_media_and_stale_agent_reply_refused(self):
        session,turn=self.recognize();self.clock.now+=21
        with self.assertRaises(RuntimeError):self.c.reply(session,turn,'Too late')
        self.c.step();self.assertIsNone(self.c.inbox)
        self.clock.now+=50;self.c.step();self.assertEqual(self.c.phase,'idle')

    def test_reply_replay_wrong_session_and_wrong_turn_refused(self):
        session,turn=self.recognize()
        for s,t in [('other',turn),(session,'other')]:
            with self.assertRaises(RuntimeError):self.c.reply(s,t,'No')
        self.c.reply(session,turn,'Once')
        with self.assertRaises(RuntimeError):self.c.reply(session,turn,'Twice')

    def test_rejected_start_does_not_cancel_current_job(self):
        session=self.ready();self.c.listen(session);self.live.feed();self.c.finish(session);job=self.c.job
        with self.assertRaises(RuntimeError):self.c.start()
        self.assertFalse(job.cancelled)

    def test_finite_capture_deadline_and_stale_transcript(self):
        session=self.ready();self.c.listen(session,1);self.live.feed();self.clock.now+=1.1;self.c.step()
        self.assertEqual(self.c.phase,'recognizing');self.clock.now+=21
        self.c.job.finish(b'{"text":"Old audio"}');self.c.step();self.assertIsNone(self.c.inbox)

    def test_limits_and_error_results(self):
        for seconds,speaker in [(0,False),(91,False),(True,False),(60,'yes')]:
            with self.assertRaises(ValueError):self.c.start(seconds,speaker)
        session=self.ready();self.c.listen(session);self.live.feed();self.c.finish(session)
        self.c.job.finish(error='failed');self.c.step();self.assertEqual(self.c.phase,'ready')
        for text in ('','x'*161,'line\nbreak'):
            with self.assertRaises(ValueError):self.c.reply(session,'turn',text)


class ProcessJobTests(unittest.TestCase):
    def wait(self,job):
        job.thread.join(timeout=3);self.assertFalse(job.thread.is_alive());self.assertTrue(job.reaped);return job.poll()
    def test_bounded_pipe_roundtrip(self):
        job=ProcessJob([sys.executable,'-c','import sys;sys.stdout.buffer.write(sys.stdin.buffer.read()[::-1])'],b'a'*100000+b'b',100001,2)
        self.assertEqual(self.wait(job),(b'b'+b'a'*100000,None))
    def test_output_limit_deadline_and_cancel_kill_and_reap(self):
        for mode in ('output','deadline','cancel'):
            with self.subTest(mode=mode):
                code='import sys;sys.stdout.write("x"*10000)' if mode=='output' else 'import time;time.sleep(30)'
                job=ProcessJob([sys.executable,'-c',code],b'',100,.2)
                if mode=='cancel':job.cancel()
                self.assertIsNotNone(self.wait(job)[1]);self.assertTrue(job.reaped)


APP_ENV=all(importlib.util.find_spec(n) for n in ('fastapi','httpx','reachy_mini'))
@unittest.skipUnless(APP_ENV,'Prepared Reachy app environment required')
class ConversationAPITests(unittest.TestCase):
    def test_authenticated_schema_bound_turn_api(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from cammy_reachy_app.main import FaceLab,configure
        live=Live();live.stop=lambda wait=False:None # no device I/O
        lab=FaceLab(live=live);app=FastAPI();configure(app,lab)
        lab.conversation.speech=Speech()
        http=TestClient(app,base_url='http://127.0.0.1:8801');headers={'X-Cammy-Lab':lab.token}
        try:
            self.assertEqual(http.get('/api/conversation/status').status_code,403)
            for value in ({'seconds':91},{'speaker':'yes'},{'unknown':1}):
                self.assertEqual(http.post('/api/conversation/start',json=value,headers=headers).status_code,400)
            self.assertEqual(http.post('/api/conversation/reply',json={},headers=headers).status_code,400)
            self.assertEqual(http.post('/api/conversation/stop',json={'session':'wrong'},headers=headers).status_code,409)
            self.assertEqual(http.get('/api/conversation/status',headers=headers).json()['phase'],'idle')
        finally:http.close();lab.close()

    def test_sdk_turn_roundtrip_through_actual_app_routes(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from cammy_reachy_app.main import FaceLab,configure
        from cammy_conversation_client import CammyConversation
        live=Live()
        def stop(wait=False):live.phase='idle';return live.status()
        live.stop=stop
        lab=FaceLab(live=live);app=FastAPI();configure(app,lab)
        lab.conversation.quit.set();lab.conversation.thread.join(timeout=1)
        lab.conversation.speech=Speech()
        http=TestClient(app,base_url='http://127.0.0.1:8801')
        class Response:
            def __init__(self,value):self.value=value
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):return self.value[:n]
        class Opener:
            def open(self,request,timeout):
                if isinstance(request,str):return Response(http.get('/').content)
                headers=dict(request.header_items())
                method=request.get_method();url=request.full_url
                response=http.request(method,url,headers=headers,content=request.data)
                if response.status_code>=400:raise RuntimeError('HTTP '+str(response.status_code))
                return Response(response.content)
        try:
            with patch('urllib.request.build_opener',return_value=Opener()):client=CammyConversation()
            self.assertEqual(live.calls,[])
            client.start(30,False);lab.conversation.step();client.listen()
            live.feed();client.release();lab.conversation.job.finish(b'{"text":"A fresh turn"}');lab.conversation.step()
            turn=client.turn();self.assertEqual(turn['text'],'A fresh turn')
            client.reply(turn['turn'],'A bound reply');self.assertIsNone(client.turn())
            client.stop();self.assertEqual(client.status()['phase'],'idle')
        finally:http.close();lab.close()
