"""Fixed boot client and startup transaction: loopback/synthetic fixtures only."""
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        raw = b''
        while b'\r\n\r\n' not in raw:
            part = self.request.recv(2048)
            if not part:
                return
            raw += part
        head, body = raw.split(b'\r\n\r\n', 1)
        lines = head.decode().split('\r\n')
        method, path, _ = lines[0].split(' ')
        headers = dict(x.split(': ', 1) for x in lines[1:])
        length = int(headers.get('Content-Length', '0'))
        while len(body) < length:
            body += self.request.recv(length - len(body))
        self.server.calls.append((method, path, headers, body))
        answer = self.server.answer(method, path, headers, body)
        try:
            self.request.sendall(answer)
        except OSError:
            pass

def http(body, status=200, extra=''):
    if not isinstance(body, str):
        body = json.dumps(body)
    b = body.encode()
    return (f'HTTP/1.1 {status} OK\r\nContent-Length: {len(b)}\r\n{extra}\r\n').encode() + b

@unittest.skipUnless(shutil.which('cc'), 'C compiler required')
class BootClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.token = cls.root/'token'
        cls.token.write_text('a'*64+'\n');cls.token.chmod(0o600)
        cls.config = cls.root/'net.lua'
        cls.config.write_text('["ssid_prefix"] = "CammyOS",\n')
        cls.servers = []
        for _ in range(3):
            s = Server(('127.0.0.1', 0), Handler)
            s.calls = []
            cls.servers.append(s)
            threading.Thread(target=s.serve_forever, daemon=True).start()
        cls.binary = cls.root/'client'
        flags = ['-std=c11','-O2','-Wall','-Wextra','-Werror','-Wformat=2','-Wshadow','-Wconversion',
            '-DTEST_LOCAL_IP',f'-DTOKEN_FILE="{cls.token}"',f'-DCONFIG_FILE="{cls.config}"',
            '-DTIMEOUT_MS=500']
        flags += [f'-D{k}={s.server_address[1]}' for k,s in zip(('PORTAL_PORT','NATIVE_PORT','RTSP_PORT'),cls.servers)]
        subprocess.run([*json.loads(os.environ.get('CAMMY_BOOT_CC','["cc"]')),*flags,str(ROOT/'src/boot/native_service_client.c'),'-o',str(cls.binary)],check=True,capture_output=True)

    @classmethod
    def tearDownClass(cls):
        for s in cls.servers:
            s.shutdown();s.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        self.owner = 'cammy-boot';self.active = False
        self.token.chmod(0o600)
        self.config.write_text('["ssid_prefix"] = "CammyOS",\n')
        for s in self.servers:
            s.calls.clear()
        self.servers[0].answer = self.portal
        self.servers[1].answer = self.native
        self.servers[2].answer = lambda *a: b'RTSP/1.0 200 OK\r\nContent-Length: 25\r\n\r\nv=0\r\nm=video 0 RTP/AVP 96'

    def portal(self, method, path, headers, body):
        self.assertEqual(headers['Authorization'],'Bearer '+'a'*64)
        self.assertEqual(headers['X-Agent-ID'],'cammy-boot')
        if path == '/v1/state':
            return http(dict(owner=self.owner,preview_active=self.active,lease_remaining_seconds=110.3))
        if path == '/v1/session':
            self.assertEqual(json.loads(body),dict(agent='cammy-boot',ttl=120))
            return http(dict(owner='cammy-boot',ttl=120))
        if path == '/v1/session/release':
            return http(dict(released=True))
        if path == '/v1/preview/start':
            return http(dict(preview_active=True,rtsp_url='rtsp://127.0.0.1:8554/ch00'),202)
        return http({},404)

    def native(self, method, path, headers, body):
        self.assertNotIn('Authorization',headers)
        if 'cmd=2005' in path:return http(dict(RecodStatus=0))
        if 'cmd=2008' in path:return http(dict(current_mode=0,curworkmodename='NormalRecordeMode'))
        if 'cmd=3003' in path:
            self.assertEqual(path,'/api/setdeviceinfo/?custom=1&cmd=3003&str=CammyOS')
            self.assertEqual(method,'POST');self.assertEqual(body,b'')
            return http(dict(Cmd=3003,Value=0))
        if 'cmd=3034' in path:return http(dict(Cmd=3034,Value=0))
        return http({},404)

    def runop(self, op, ok=True):
        p = subprocess.run([*json.loads(os.environ.get('CAMMY_BOOT_RUNNER','[]')),str(self.binary),op],capture_output=True,timeout=3)
        self.assertEqual(p.returncode,0 if ok else 1,p.stderr)
        self.assertNotIn(b'a'*64,p.stdout+p.stderr)
        return p

    def test_fixed_operations_and_lease_binding(self):
        for op in ('preflight','init','photo','video','mode-video','start','release','sdp'):
            with self.subTest(op=op):self.runop(op)
        self.owner = None;self.runop('acquire');self.runop('idle')
        paths = [x[1] for x in self.servers[1].calls]
        self.assertFalse(any('1100' in x or '3004' in x for x in paths))

    def test_expired_or_malformed_prepare_deadline_prevents_any_network_request(self):
        from unittest.mock import patch
        for raw in ['', '0', 'bad', str(int((time.monotonic()+100)*1000))]:
            with self.subTest(deadline=raw),patch.dict(os.environ,CAMMY_PREPARE_DEADLINE_MS=raw):
                for op in ['acquire','init','photo','video','start']:self.runop(op,False)
                self.assertTrue(all(not server.calls for server in self.servers))
    def test_reserved_owner_renew_uses_remaining_preparation_ttl(self):
        from unittest.mock import patch
        original=self.portal
        def portal(method,path,headers,body):
            if path=='/v1/session':
                request=json.loads(body);self.assertEqual(request['agent'],'cammy-boot');self.assertTrue(1<=request['ttl']<=20)
                return http(dict(owner='cammy-boot',ttl=request['ttl']))
            return original(method,path,headers,body)
        self.servers[0].answer=portal
        with patch.dict(os.environ,CAMMY_PREPARE_DEADLINE_MS=str(int((time.monotonic()+20)*1000))):
            self.runop('acquire')
        self.owner='other';self.runop('acquire',False)

    def test_stock_close_delimited_native_responses(self):
        original=self.native
        def closed(*args):
            answer=original(*args)
            _,body=answer.split(b'\r\n\r\n',1)
            return b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n'+body
        self.servers[1].answer=closed
        self.runop('preflight');self.runop('init')
        self.servers[1].answer=lambda *a:b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n{"RecodStatus":'
        self.runop('init',False)

    def test_foreign_owner_or_preview_blocks_native_mutation(self):
        for owner,active in [('other',False),('cammy-boot',True),(None,False)]:
            self.owner,self.active=owner,active
            for op in ('init','photo','video'):
                self.runop(op,False)
        self.assertEqual(self.servers[1].calls,[])

    def test_recording_or_wrong_prefix_refuses_init(self):
        self.servers[1].answer=lambda *a:http(dict(RecodStatus=1))
        self.runop('init',False)
        self.assertEqual(len(self.servers[1].calls),1)
        self.servers[1].answer=self.native
        self.config.write_text('["ssid_prefix"] = "Another",\n')
        self.runop('init',False)
        self.assertFalse(any('3003' in x[1] for x in self.servers[1].calls))

    def test_prefix_with_internal_space_or_duplicate_key_is_not_renamed(self):
        for text in ('["ssid_prefix"] = "Cammy OS",\n','["ssid_prefix"]="CammyOS",\n["ssid_prefix"]="CammyOS",\n'):
            self.config.write_text(text)
            self.runop('init',False)
        self.assertFalse(any('3003' in x[1] for x in self.servers[1].calls))

    def test_bad_framing_json_duplicate_and_type_refused(self):
        answers = [http('{"owner":"cammy-boot","owner":"cammy-boot","preview_active":false}'),
            http('{"owner":"cammy-boot","preview_active":"false"}'),
            http('{"owner":"cammy-boot","preview_active":false} junk'),
            http('{}',extra='Content-Length: 2\r\n'),http('{}',extra='Transfer-Encoding: chunked\r\n'),
            b'HTTP/1.1 200 OK\r\nContent-Length: 999999\r\n\r\n',
            b'HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\n{}',http({},302)]
        for answer in answers:
            self.servers[0].answer=lambda *a,answer=answer:answer
            self.runop('init',False)
        self.assertEqual(self.servers[1].calls,[])

    def test_token_permissions_symlink_and_unknown_op(self):
        self.token.chmod(0o644);self.runop('init',False)
        self.token.chmod(0o600)
        saved=self.token.with_suffix('.saved');self.token.rename(saved)
        self.token.symlink_to(saved)
        try:self.runop('init',False)
        finally:self.token.unlink();saved.rename(self.token)
        p=subprocess.run([*json.loads(os.environ.get('CAMMY_BOOT_RUNNER','[]')),str(self.binary),'shell'],capture_output=True)
        self.assertEqual(p.returncode,2)
        self.assertEqual(self.servers[1].calls,[])

    def test_single_uncertain_request_has_absolute_deadline(self):
        def slow(*a):time.sleep(.8);return http(dict(Cmd=3003,Value=0))
        def route(*a):return slow(*a) if '3003' in a[1] else self.native(*a)
        self.servers[1].answer=route
        start=time.monotonic();self.runop('init',False)
        self.assertLess(time.monotonic()-start,.75)
        self.assertEqual(sum('3003' in x[1] for x in self.servers[1].calls),1)

class BootTransactionTests(unittest.TestCase):
    """Relocated real transaction, synthetic clients; never run hardware prefixes."""
    def run_transaction(self, fail='', recovery='restored', mode='start', twice=False):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        root=Path(tmp.name);r=root/'runtime';r.mkdir();proc=root/'proc';proc.mkdir()
        (r/'state').write_text('HOME_STATION\n')
        (r/'wpa.pid').write_text('12');(r/'dhcp.pid').write_text('13')
        (proc/'uptime').write_text('1000.0 0\n')
        (proc/'sys/kernel/random').mkdir(parents=True)
        (proc/'sys/kernel/random/boot_id').write_text('test-boot\n')
        mic=proc/'asound/card0/pcm0c/sub0';mic.mkdir(parents=True);(mic/'status').write_text('closed\n')
        client=r/'native-service-client'
        client.write_text('''#!/bin/sh
printf '%s\n' "$1" >>"'''+str(root/'calls')+'''"
[ "$1" != "'''+fail+'''" ] || exit 1
case "$1" in ip) echo 192.0.2.53;; esac
exit 0
''');client.chmod(0o700)
        busy=r/'busybox';busy.write_text('#!/bin/sh\necho 8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec\n');busy.chmod(0o700)
        daemon=r/'daemon-exec';daemon.write_text('''#!/bin/sh
[ "$#" = 5 ] || exit 90
case "$1" in */recovery.log) ;; *) exit 91;; esac
[ "$2" = /bin/sh ] && [ "$4" = recover ] || exit 92
'''.replace('"$#" = 5','"$#" = 4')+f'echo ready >"{r}/native-video/ready"\necho {recovery} >"{r}/native-video/recovery"\n');daemon.chmod(0o700)
        source=(ROOT/'scripts/native-media-start.sh').read_text().replace('/tmp/cammy-runtime',str(r)).replace('/proc/',str(proc)+'/')
        # Proc ownership is separately exercised below. This fixture isolates the
        # coordinator/action ordering, preserving all transaction code.
        source=source.replace('if [ "${1:-}" = recover ];then', '''owned(){ return 0; }
starttime(){ echo 1; }
station(){ return 0; }
ap_helpers(){ return 0; }
pidof(){ echo 11; }
sleep(){ :; }
if [ "${1:-}" = recover ];then''')
        script=r/'native-media-start.sh';script.write_text(source)
        p=subprocess.run(['dash',str(script),mode],capture_output=True,timeout=3)
        if twice:
            subprocess.run(['dash',str(script),mode],check=True,capture_output=True,timeout=3)
        calls=(root/'calls').read_text().splitlines() if (root/'calls').exists() else []
        state=(r/'native-video.state').read_text().strip() if (r/'native-video.state').exists() else None
        return p,calls,state

    def test_success_order_release_idle_and_one_attempt(self):
        p,calls,state=self.run_transaction(twice=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(state,'sdp-ready-idle')
        self.assertEqual(calls.count('init'),1)
        self.assertEqual([x for x in calls if x in ('acquire','init','photo','video','start','release')],['acquire','init','photo','video','start','release'])
        self.assertEqual(calls[-1],'idle')

    def test_busy_or_preflight_refusal_never_initializes(self):
        for failure in ('acquire','preflight'):
            with self.subTest(failure=failure):
                p,calls,state=self.run_transaction(fail=failure)
                self.assertNotIn('init',calls)
                self.assertEqual('release' in calls,failure=='preflight')

    def test_uncertain_init_never_retries_or_reloads(self):
        p,calls,state=self.run_transaction(fail='init')
        self.assertEqual(state,'native-ack-uncertain')
        self.assertEqual(calls.count('init'),1)
        self.assertNotIn('photo',calls)
        self.assertIn('release',calls)

    def test_recovery_failure_prevents_media_reload(self):
        p,calls,state=self.run_transaction(recovery='recovery-required')
        self.assertEqual(state,'recovery-required')
        self.assertNotIn('photo',calls)
        self.assertIn('release',calls)

    def test_mode_failure_attempts_video_restore_and_releases(self):
        p,calls,state=self.run_transaction(fail='mode-photo')
        self.assertEqual(state,'mode-reload-failed')
        self.assertIn('video',calls)
        self.assertEqual(calls[-1],'release')
        self.assertNotIn('start',calls)

    def test_sdp_unavailable_still_releases_to_idle(self):
        p,calls,state=self.run_transaction(fail='sdp')
        self.assertEqual(state,'sdp-unavailable-idle')
        self.assertEqual(calls.count('sdp'),8)
        self.assertIn('release',calls)

    def test_startup_opt_in_runs_before_network_supervision(self):
        source=(ROOT/'scripts/tf-runtime-startup.sh').read_text()
        self.assertIn('experimental-native-video-v1',source)
        self.assertLess(source.index('start_portal || cleanup'),source.index('phase native_media'))
        self.assertLess(source.index('phase native_media'),source.index('MISSES=0'))

    def test_helper_cleanup_checks_exact_identity_and_new_start(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        root=Path(tmp.name);proc=root/'proc';proc.mkdir()
        for pid,name,tick,args,exe in ((10,'udhcpd',200,'/usr/sbin/udhcpd /etc/udhcpd.conf','/bin/busybox'),
            (11,'udhcpd',20,'/usr/sbin/udhcpd /etc/udhcpd.conf','/bin/busybox'),
            (12,'udhcpd',200,'/usr/sbin/udhcpd /tmp/other.conf','/bin/busybox'),
            (13,'hostapd',200,'hostapd /etc/ap.conf','/bin/busybox')):
            d=proc/str(pid);d.mkdir();(d/'comm').write_text(name+'\n')
            fields=[str(pid),'('+name+')','S']+['0']*18+[str(tick)]
            (d/'stat').write_text(' '.join(fields)+'\n')
            (d/'cmdline').write_bytes(args.replace(' ','\0').encode()+b'\0')
            (d/'exe').symlink_to(exe)
        source=(ROOT/'scripts/native-media-start.sh').read_text()
        functions=source[source.index('starttime(){'):source.index('recover(){')].replace('/proc/',str(proc)+'/')
        out=root/'killed'
        p=subprocess.run(['dash','-c',functions+f'\nMIN_START=100\nkill(){{ echo "$*" >>"{out}"; }}\nap_helpers stop; exit 0\n'],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(out.read_text().splitlines(),['-KILL 10'])

class RecoveryTests(unittest.TestCase):
    def run_recovery(self, reused=False, lost_station=False):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name);r=root/'runtime';d=r/'native-video';d.mkdir(parents=True)
        proc=root/'proc';(proc/'sys/kernel/random').mkdir(parents=True)
        (proc/'sys/kernel/random/boot_id').write_text('boot\n')
        for pid,tick,exe in ((11,1,'/usr/bin/sdvcam'),(12,2,str(r/'wpa_supplicant')),(13,3,str(r/'busybox')),(14,200,'/bin/busybox')):
            p=proc/str(pid);p.mkdir()
            name='udhcpd' if pid==14 else 'fixture'
            (p/'comm').write_text(name+'\n')
            fields=[str(pid),'('+name+')','S']+['0']*18+[str(tick+1 if reused and pid==13 else tick)]
            (p/'stat').write_text(' '.join(fields)+'\n')
            (p/'exe').symlink_to(exe)
            (p/'cmdline').write_bytes(b'/usr/sbin/udhcpd\0/etc/udhcpd.conf\0')
        (d/'identity').write_text('boot 11 1 12 2 13 3 192.0.2.53 100\n')
        (d/'go').touch();(d/'command.done').touch();(r/'dhcp.pid').write_text('13\n')
        address=root/'address';address.write_text('192.168.10.1\n')
        c=r/'native-service-client';c.write_text('#!/bin/sh\ncat "'+str(address)+'"\n');c.chmod(0o700)
        w=r/'wpa_cli';w.write_text('#!/bin/sh\necho "wpa_state='+('SCANNING' if lost_station else 'COMPLETED')+'"\n');w.chmod(0o700)
        source=(ROOT/'scripts/native-media-start.sh').read_text()
        source=source[:source.index('if [ "${1:-}" = recover ];then')].replace('/tmp/cammy-runtime',str(r)).replace('/proc/',str(proc)+'/')
        signals=root/'signals'
        source+='\nsleep(){ :; }\nkill(){\necho "$*" >>"'+str(signals)+'"\ncase "$1:$2" in -KILL:14) rm -rf "'+str(proc/'14')+'";; -USR1:13) echo 192.0.2.53 >"'+str(address)+'";; *) exit 97;; esac\n}\nrecover\n'
        p=subprocess.run(['dash','-c',source],capture_output=True,text=True,timeout=3)
        return p,(d/'recovery').read_text().strip(),signals.read_text().splitlines() if signals.exists() else []

    def test_independent_guard_stops_only_new_native_dhcp_and_renews_owned_client(self):
        p,state,signals=self.run_recovery()
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(state,'restored')
        self.assertEqual(signals,['-KILL 14','-USR1 13'])

    def test_pid_reuse_prevents_all_signals(self):
        p,state,signals=self.run_recovery(reused=True)
        self.assertEqual(state,'recovery-required')
        self.assertEqual(signals,[])

    def test_unassociated_station_never_claims_restored(self):
        p,state,signals=self.run_recovery(lost_station=True)
        self.assertEqual(state,'recovery-required')
        self.assertEqual(signals,['-KILL 14'])
