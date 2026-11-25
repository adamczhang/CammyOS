"""Loopback wire fixtures; no camera, microphone or device access."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
ENV=importlib.util.find_spec('websockets') is not None
if ENV:
    from cammy_signalling import SignallingServer
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus,ConnectionClosed


@unittest.skipUnless(ENV,'Websockets runtime required')
class SignallingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server=SignallingServer('fixture-session',port=0,max_sessions=1)
        self.server.start();self.url=f'ws://127.0.0.1:{self.server.port}/'
    async def asyncTearDown(self):
        self.assertTrue(await asyncio.to_thread(self.server.stop))
        self.assertIsNone(self.server.failure)
    async def hello(self,producer=False):
        socket=await connect(self.url,additional_headers={'X-Cammy-Producer':self.server.producer_token} if producer else {})
        welcome=json.loads(await socket.recv())
        self.assertEqual(welcome['type'],'welcome')
        return socket,welcome['peerId']
    async def register(self,socket):
        await socket.send(json.dumps({'type':'setPeerStatus','roles':['producer'],
                                     'meta':{'name':'reachymini','cammy_session':'fixture-session'}}))
    async def test_any_browser_origin_and_wrong_path_refused(self):
        for origin in ('http://evil.example','http://127.0.0.1:8801','null'):
            with self.assertRaises(InvalidStatus):
                async with connect(self.url,origin=origin):pass
        with self.assertRaises(InvalidStatus):
            async with connect(self.url+'other'):pass
        self.assertFalse(self.server.advertised.is_set())
    async def test_producer_auth_session_binding_and_default_list_format(self):
        socket,peer=await self.hello()
        try:
            await self.register(socket)
            self.assertEqual(json.loads(await socket.recv())['type'],'error')
            await socket.send('{"type":"list"}')
            self.assertEqual(json.loads(await socket.recv()),{'type':'list','producers':[]})
        finally:await socket.close()
        producer,pid=await self.hello(True)
        try:
            await self.register(producer)
            reader,_=await self.hello()
            try:
                await reader.send('{"type":"list"}')
                value=json.loads(await reader.recv())
                self.assertEqual(value['producers'][0]['id'],pid)
                self.assertEqual(value['producers'][0]['meta']['cammy_session'],'fixture-session')
            finally:await reader.close()
        finally:await producer.close()
    async def test_typed_offer_answer_ice_relay_and_participant_only_end(self):
        producer,pid=await self.hello(True);consumer,cid=await self.hello();outsider,_=await self.hello()
        try:
            await self.register(producer)
            await consumer.send(json.dumps({'type':'startSession','peerId':pid}))
            value=json.loads(await consumer.recv());sid=value['sessionId']
            self.assertEqual(value,{'type':'sessionStarted','peerId':pid,'sessionId':sid})
            self.assertEqual(json.loads(await producer.recv()),{'type':'startSession','peerId':cid,'sessionId':sid,'offer':None})
            for sender,receiver,payload in ((producer,consumer,{'sdp':{'type':'offer','sdp':'v=0\r\n'}}),
                    (consumer,producer,{'sdp':{'type':'answer','sdp':'v=0\r\n'}}),
                    (consumer,producer,{'ice':{'candidate':'candidate:fixture','sdpMLineIndex':0}})):
                message={'type':'peer','sessionId':sid,**payload}
                await sender.send(json.dumps(message));self.assertEqual(json.loads(await receiver.recv()),message)
            await outsider.send(json.dumps({'type':'endSession','sessionId':sid}))
            self.assertEqual(json.loads(await outsider.recv())['type'],'error')
            self.assertEqual(len(self.server.sessions),1)
            await consumer.close()
            self.assertEqual(json.loads(await producer.recv()),{'type':'endSession','sessionId':sid})
        finally:
            for socket in (producer,consumer,outsider):await socket.close()
    async def test_invalid_fields_and_session_limit_do_not_allocate(self):
        producer,pid=await self.hello(True);consumer,_=await self.hello();other,_=await self.hello()
        try:
            await self.register(producer)
            await consumer.send(json.dumps({'type':'startSession','peerId':pid,'offer':'unapproved'}))
            self.assertEqual(json.loads(await consumer.recv())['type'],'error')
            self.assertFalse(self.server.sessions)
            await consumer.send(json.dumps({'type':'startSession','peerId':pid}))
            sid=json.loads(await consumer.recv())['sessionId'];await producer.recv()
            await other.send(json.dumps({'type':'startSession','peerId':pid}))
            self.assertEqual(json.loads(await other.recv())['type'],'error')
            self.assertEqual(len(self.server.sessions),1)
            await consumer.send(json.dumps({'type':'peer','sessionId':sid,'ice':{'candidate':'x','sdpMLineIndex':-1}}))
            self.assertEqual(json.loads(await consumer.recv())['type'],'error')
            await consumer.send(json.dumps({'type':'endSession','sessionId':sid}))
            await consumer.recv();await producer.recv()
        finally:
            for socket in (producer,consumer,other):await socket.close()
    async def test_slow_send_retains_socket_when_peer_is_removed_during_await(self):
        server=self.server
        class SlowSocket:
            closed=False
            async def send(self,value):
                server.peers.pop('removed-peer')
                raise TimeoutError()
            async def close(self,**kwargs):self.closed=True
        socket=SlowSocket()
        server.peers['removed-peer']={'socket':socket}
        await server._send('removed-peer',{'type':'fixture'})
        self.assertTrue(socket.closed)

    async def test_stop_closes_native_peers(self):
        reader,_=await self.hello()
        self.assertTrue(await asyncio.to_thread(self.server.stop))
        with self.assertRaises(ConnectionClosed):await reader.recv()
