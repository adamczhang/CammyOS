"""Bounded native-client admission for GStreamer's existing signalling protocol.

Wire schema: gst-plugins-rs 0.15.4, net/webrtc/protocol/src/lib.rs (MPL-2.0).
This is an independent protocol implementation, not a new media protocol. Native
loopback clients omit Origin; browser handshakes with ANY Origin are refused.
Only the per-launch authenticated producer may advertise the bound Cammy session.
"""
import asyncio
from collections import deque
import hmac
import http
import json
import secrets
import threading
import time
import uuid

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed


class SignallingServer:
    def __init__(self, session, port=8443, max_clients=6, max_sessions=2):
        if not isinstance(session, str) or not session or len(session)>80:
            raise ValueError('Bound session required')
        self.session = session
        self.port = port
        self.max_clients, self.max_sessions = max_clients, max_sessions
        self.producer_token = secrets.token_urlsafe(32)
        self.peers, self.sessions = {}, {}
        self.producer = None
        self.ready = threading.Event()
        self.advertised = threading.Event()
        self.done = threading.Event()
        self.failure = None
        self.thread = self.loop = self.server = self.finish = None
        self.counts = {'accepted':0,'rejected':0,'sessions_started':0,'sessions_ended':0}

    def start(self):
        if self.thread is not None:
            raise RuntimeError('Signalling instance is single-use')
        self.thread = threading.Thread(target=self._thread_main,daemon=True,name='cammy-signalling')
        self.thread.start()
        if not self.ready.wait(3) or self.failure:
            self.stop()
            raise RuntimeError('Loopback signalling server did not start')

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self.failure = type(exc).__name__
            self.ready.set()
        finally:
            self.advertised.clear()
            self.done.set()

    async def _run(self):
        self.loop = asyncio.get_running_loop()
        self.finish = asyncio.Event()
        async with serve(self._connection,'127.0.0.1',self.port,origins=[None],
                         process_request=self._admit,max_size=65536,max_queue=8,
                         write_limit=32768,ping_interval=10,ping_timeout=5,
                         open_timeout=2,close_timeout=1,compression=None) as server:
            self.server = server
            self.port = server.sockets[0].getsockname()[1]
            self.ready.set()
            await self.finish.wait()
            server.close(close_connections=True)
            await server.wait_closed()
        self.server = None

    def _admit(self, connection, request):
        hosts = (f'127.0.0.1:{self.port}',f'localhost:{self.port}')
        if (request.path != '/' or request.headers.get('Host') not in hosts
                or request.headers.get('Origin') is not None):
            self.counts['rejected'] += 1
            return connection.respond(http.HTTPStatus.FORBIDDEN,'Native loopback clients only\n')
        if len(self.peers)>=self.max_clients:
            self.counts['rejected'] += 1
            return connection.respond(http.HTTPStatus.SERVICE_UNAVAILABLE,'Peer limit\n')
        return None

    async def _send(self, peer, value):
        row=self.peers.get(peer)
        if row is None:
            return
        socket=row['socket']
        try:
            await asyncio.wait_for(socket.send(json.dumps(value)),1)
        except (TimeoutError, ConnectionClosed):
            await socket.close(code=1011,reason='Slow or disconnected peer')

    async def _changed(self, peer, roles, meta):
        value={'type':'peerStatusChanged','peerId':peer,'roles':roles,'meta':meta}
        await asyncio.gather(*(self._send(key,value) for key,row in tuple(self.peers.items())
                               if 'listener' in row['roles']))

    async def _end(self, session):
        pair = self.sessions.pop(session,None)
        if pair:
            self.counts['sessions_ended'] += 1
            await asyncio.gather(*(self._send(peer,{'type':'endSession','sessionId':session}) for peer in pair))

    async def _connection(self, websocket):
        if len(self.peers)>=self.max_clients:
            await websocket.close(code=1013,reason='Peer limit')
            return
        peer=str(uuid.uuid4())
        authorized=hmac.compare_digest(websocket.request.headers.get('X-Cammy-Producer',''),self.producer_token)
        self.peers[peer]={'socket':websocket,'producer_authorized':authorized,'roles':[],
                          'meta':None,'times':deque(),'bad':0}
        self.counts['accepted']+=1
        try:
            await self._send(peer,{'type':'welcome','peerId':peer})
            async for raw in websocket:
                row=self.peers[peer]
                now=time.monotonic();row['times'].append(now)
                while row['times'] and now-row['times'][0]>=1:row['times'].popleft()
                if len(row['times'])>64:
                    await websocket.close(code=1008,reason='Message rate limit');break
                try:
                    if not isinstance(raw,str):raise ValueError('Text messages required')
                    value=json.loads(raw)
                    if not isinstance(value,dict):raise ValueError('Message object required')
                    await self._message(peer,value)
                except (ValueError,TypeError,KeyError):
                    row['bad']+=1
                    await self._send(peer,{'type':'error','details':'Invalid or unauthorized signalling message'})
                    if row['bad']>=3:
                        await websocket.close(code=1008,reason='Repeated invalid messages');break
        except ConnectionClosed:
            pass
        except Exception as exc:
            self.failure=type(exc).__name__
        finally:
            for sid,pair in tuple(self.sessions.items()):
                if peer in pair:await self._end(sid)
            old=self.peers.pop(peer,None)
            if self.producer==peer:
                self.producer=None;self.advertised.clear()
            if old and old['roles']:await self._changed(peer,[],old['meta'])

    @staticmethod
    def _fields(value,allowed,required):
        if value.keys()-set(allowed) or not set(required)<=value.keys():
            raise ValueError('Unsupported message fields')

    async def _message(self,peer,value):
        kind=value['type'];row=self.peers[peer]
        if kind in ('list','listConsumers'):
            self._fields(value,('type',),('type',))
            role='producer' if kind=='list' else 'consumer'
            rows=[{'id':key,'meta':item['meta']} for key,item in self.peers.items() if role in item['roles']]
            await self._send(peer,{'type':kind,'producers' if kind=='list' else 'consumers':rows})
        elif kind=='setPeerStatus':
            self._fields(value,('type','roles','meta','peerId'),('type','roles'))
            roles=value['roles'];meta=value.get('meta')
            if (not isinstance(roles,list) or len(roles)>3 or len(set(roles))!=len(roles)
                    or any(r not in ('producer','consumer','listener') for r in roles)
                    or value.get('peerId',peer)!=peer):raise ValueError('Invalid roles')
            if meta is not None and (not isinstance(meta,dict) or len(json.dumps(meta))>2048):
                raise ValueError('Bounded metadata required')
            if 'producer' in roles:
                if (not row['producer_authorized'] or self.producer not in (None,peer)
                        or 'consumer' in roles or not isinstance(meta,dict)
                        or meta.get('name')!='reachymini' or meta.get('cammy_session')!=self.session):
                    raise ValueError('Wrong producer/session')
                self.producer=peer;self.advertised.set()
            elif self.producer==peer:
                self.producer=None;self.advertised.clear()
                for sid,pair in tuple(self.sessions.items()):
                    if peer in pair:await self._end(sid)
            row['roles'],row['meta']=roles,meta
            await self._changed(peer,roles,meta)
        elif kind=='startSession':
            self._fields(value,('type','peerId','offer'),('type','peerId'))
            target=value['peerId']
            if (not isinstance(target,str) or target!=self.producer or target==peer
                    or peer==self.producer or value.get('offer') is not None
                    or len(self.sessions)>=self.max_sessions
                    or any(peer in pair for pair in self.sessions.values())):
                raise ValueError('Session target unavailable')
            sid=str(uuid.uuid4());self.sessions[sid]=(peer,target)
            self.counts['sessions_started']+=1
            await self._send(peer,{'type':'sessionStarted','peerId':target,'sessionId':sid})
            await self._send(target,{'type':'startSession','peerId':peer,'sessionId':sid,'offer':None})
        elif kind in ('peer','endSession'):
            self._fields(value,('type','sessionId','ice','sdp') if kind=='peer' else ('type','sessionId'),('type','sessionId'))
            sid=value['sessionId']
            if not isinstance(sid,str) or sid not in self.sessions or peer not in self.sessions[sid]:
                raise ValueError('Not a session participant')
            if kind=='endSession':await self._end(sid);return
            if ('ice' in value)+('sdp' in value)!=1:raise ValueError('One peer payload required')
            payload=value.get('ice',value.get('sdp'))
            if not isinstance(payload,dict):raise ValueError('Peer object required')
            if 'ice' in value:
                self._fields(payload,('candidate','sdpMLineIndex'),('candidate','sdpMLineIndex'))
                if (not isinstance(payload['candidate'],str) or len(payload['candidate'])>4096
                        or type(payload['sdpMLineIndex']) is not int or not 0<=payload['sdpMLineIndex']<16):
                    raise ValueError('Invalid ICE')
            else:
                self._fields(payload,('type','sdp'),('type','sdp'))
                if (payload['type'] not in ('offer','answer') or not isinstance(payload['sdp'],str)
                        or len(payload['sdp'])>60000):raise ValueError('Invalid SDP')
            target=next(key for key in self.sessions[sid] if key!=peer)
            await self._send(target,value)
        else:raise ValueError('Unsupported signalling command')

    def stop(self):
        if self.loop is not None and self.finish is not None and not self.done.is_set():
            self.loop.call_soon_threadsafe(self.finish.set)
        if self.thread and self.thread is not threading.current_thread():self.thread.join(4)
        return self.done.is_set()

    def status(self):
        return {'bound_host':'127.0.0.1','port':self.port,'protocol':'GStreamer default signalling',
                'producer_ready':self.advertised.is_set(),'consumers':len(self.sessions),
                'browser_origins_allowed':False,'failure':self.failure,**self.counts}
