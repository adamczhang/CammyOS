"""cammy-ui (0.7 touch menu): screen logic, rendering, and the real backend against mock
wpa_supplicant and portal servers. No camera needed.

python3 -m unittest tests.test_cammy_ui
"""
import http.server
import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/cammy-ui'
QEMU = ROOT.parent / 'Cammy Bot/tools/build-deps/usr/bin/qemu-arm'
SCAN = ('bssid / frequency / signal level / flags / ssid\n'
        'aa:aa:aa:aa:aa:01\t2412\t-80\t[WPA2-PSK-CCMP][ESS]\tZhangHome\n'
        'aa:aa:aa:aa:aa:02\t5180\t-50\t[WPA2-PSK-CCMP][ESS]\tZhangHome\n'
        'aa:aa:aa:aa:aa:03\t2437\t-70\t[ESS]\tCoffee Guest\n'
        'aa:aa:aa:aa:aa:04\t2462\t-66\t[WPA2-SAE-CCMP][ESS]\tCaf\\xc3\\xa9 \\"Q\\"\n'
        'aa:aa:aa:aa:aa:05\t2462\t-40\t[WPA2-PSK-CCMP][ESS]\t\n')


class MockWpa(threading.Thread):
    def __init__(self, path):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(path)
        self.sock.settimeout(0.2)
        self.commands, self.done = [], False

    def reply(self, cmd):
        if cmd == 'STATUS':
            return 'bssid=aa:aa:aa:aa:aa:02\nssid=ZhangHome\nwpa_state=COMPLETED\nip_address=10.0.0.9\n'
        if cmd == 'SIGNAL_POLL':
            return 'RSSI=-58\nLINKSPEED=72\n'
        if cmd == 'SCAN_RESULTS':
            return SCAN
        if cmd == 'ADD_NETWORK':
            return '3\n'
        return 'OK\n'

    def run(self):
        while not self.done:
            try:
                data, peer = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            cmd = data.decode()
            self.commands.append(cmd)
            self.sock.sendto(self.reply(cmd).encode(), peer)


class Portal(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):
        Portal.seen.append((self.path, self.headers.get('Authorization')))
        body = json.dumps({'version': '0.7.0', 'owner': 'gemini-live', 'owner_label': 't3 6f2c1a9e Cammy brain',
                           'owner_address': '192.168.1.20', 'owner_since_seconds': 754, 'lease_remaining_seconds': 20.5}, separators=(',', ':'))  # portal style
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body.encode() + b'\n')

    def log_message(self, *args):
        pass


class CammyUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['make', '-s', '-C', str(ROOT / 'native/ui'), 'all'], check=True, timeout=600)

    def test_screens_host_asan_and_arm_render_identically(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = {'host': [str(OUT / 'ui-fixture')], 'asan': [str(OUT / 'ui-asan')],
                    'arm': [str(QEMU), '-cpu', 'cortex-a7', str(OUT / 'ui-fixture-arm')]}
            shots = {}
            for name, cmd in runs.items():
                d = Path(tmp) / name
                d.mkdir()
                result = json.loads(subprocess.check_output(cmd + [str(d)], timeout=120))
                self.assertGreater(result['checks'], 40)
                self.assertEqual(result['failed'], 0, name)
                shots[name] = {p.name: p.read_bytes() for p in d.glob('*.ppm')}
            self.assertEqual(len(shots['host']), 24)  # 19 menu screens + 5 loading states
            self.assertEqual(shots['host'], shots['arm'])
            self.assertEqual(shots['host'], shots['asan'])

    def backend(self, kind):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / 'ctrl').mkdir()
            (run / 'state').write_text('HOME_STATION\n')
            (run / 'lease.txt').write_text('ip=192.168.1.50\nsubnet=255.255.255.0\nrouter=192.168.1.1\n')
            original = 'ctrl_interface=/tmp/cammy-runtime/ctrl\nnetwork={\n\tssid="ZhangHome"\n\tpsk="secret12"\n}\n'
            (run / 'home.conf').write_text(original)
            (run / 'portal.token').write_text('t' * 40 + '\n')
            wpa = MockWpa(str(run / 'ctrl/wlan0'))
            wpa.start()
            server = http.server.HTTPServer(('127.0.0.1', 0), Portal)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                outs = [json.loads(subprocess.check_output([str(OUT / 'backend-fixture'), str(run), str(server.server_port), kind],
                                                           timeout=60)) for _ in range(2)]
            finally:
                wpa.done = True
                server.shutdown()
            self.pack_text = (run / 'face-pack').read_text()
            return outs, wpa.commands, (run / 'home.conf').read_text(), (run / 'home.conf.before-menu').read_text(), \
                original, (run / 'volume').read_text(), sorted(p.name for p in (run / 'ctrl').iterdir())

    def test_real_backend_secured_join(self):
        outs, cmds, home, backup, original, volume, ctrl = self.backend('secured')
        out = outs[0]
        self.assertEqual((out['mode'], out['connected'], out['ssid'], out['ip'], out['signal']), (1, 1, 'ZhangHome', '192.168.1.50', 3))
        self.assertEqual((out['scan'], out['early']), (0, -1))
        # Deduplicated (strongest kept), strongest first, escapes decoded, hidden SSID dropped.
        self.assertEqual(out['nets'], [['ZhangHome', 1, 3], ['Café "Q"', 1, 2], ['Coffee Guest', 0, 2]])
        self.assertEqual((out['join'], out['commit']), (0, 0))
        hexssid = 'Lab "5G"'.encode().hex()
        self.assertIn('SET_NETWORK 3 ssid %s' % hexssid, cmds)
        self.assertIn('SET_NETWORK 3 psk "Pika"2026"', cmds)
        self.assertIn('SELECT_NETWORK 3', cmds)
        self.assertIn('ENABLE_NETWORK all', cmds)
        # Persisted once (second run replaced its own block), original kept, backup made.
        self.assertTrue(home.startswith(original))
        self.assertEqual(home.count('# cammy-ui ssid=%s' % hexssid), 1)
        self.assertIn('network={\n\tssid=%s\n\tpsk="Pika"2026"\n\tpriority=10\n}\n' % hexssid, home)
        self.assertEqual(backup, original)
        self.assertEqual((out['vol'], out['vol_read'], volume), (0, 40, '40\n'))
        self.assertEqual(out['agent'], [1, 'gemini-live', 't3 6f2c1a9e Cammy brain', '192.168.1.20', 754])
        self.assertEqual(Portal.seen[-1], ('/v1/state', 'Bearer ' + 't' * 40))
        self.assertEqual(out['camera'], [1, 0])
        # Face pack: unset reads Pikachu; Fin and Fur written and published; unknown index refused.
        self.assertEqual(out['pack'], [0, 0, -1, 2, 3, 1])  # one physical pick counted (pack_seq)
        self.assertEqual(self.pack_text, 'cammy\n')
        self.assertEqual(outs[1]['pack'][0], 2)  # the second run reads the saved choice
        self.assertEqual(ctrl, ['wlan0'])  # the client socket file is removed on close

    def test_real_backend_open_network(self):
        outs, cmds, home, *_ = self.backend('open')
        self.assertEqual(outs[0]['join'], 0)
        self.assertIn('SET_NETWORK 3 key_mgmt NONE', cmds)
        self.assertIn('ssid=%s\n\tkey_mgmt=NONE' % b'Coffee Guest'.hex(), home)

    def test_loading_gate_reads_only_this_boots_records(self):
        # ui_boot.h stages: 0 stock, 1 radio, 2 Wi-Fi, 3 hotspot, 4 services, 5 ready, 6 failed.
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            cfg = run / 'cfg'
            cfg.mkdir()
            (run / 'release-health').mkdir()
            (run / 'media-prepare').mkdir()
            stand_in = run / 'stand-in'
            subprocess.run(['cc', '-x', 'c', '-o', stand_in, '-'], check=True,
                           input=b'#include <unistd.h>\nint main(void) { sleep(30); return 0; }\n')

            def named_process(name):
                executable = run / name
                os.link(stand_in, executable)
                return subprocess.Popen([executable])

            portal = named_process('cammy-portal')
            other = named_process('other')
            try:
                def view():
                    return json.loads(subprocess.check_output([str(OUT / 'backend-fixture'), str(run), 'boot'], timeout=20))
                v = view()
                self.assertEqual((v['guard'], v['stage'], v['portal']), (0, 0, 0))
                startup_progress = v['progress']
                (cfg / 'startup.pending').write_text('phase=dhcp\nboot_id=aaaa-boot\n')
                v = view()
                self.assertEqual((v['guard'], v['phase'], v['stage'], v['step']), (1, 'dhcp', 2, 1))
                self.assertGreater(v['progress'], startup_progress)
                wifi_progress = v['progress']
                (run / 'state').write_text('HOME_STATION\n')
                (run / 'portal.pid').write_text('%d\n' % other.pid)
                (cfg / 'startup.pending').write_text('phase=idle_ready\nboot_id=aaaa-boot\n')
                self.assertEqual(view()['stage'], 4)  # a pid that is not the portal
                (run / 'portal.pid').write_text('%d\n' % portal.pid)
                self.assertEqual(view()['stage'], 4)  # idle_ready still needs health
                # The fallback access point counts only with its servers running, as health checks.
                (run / 'state').write_text('AP_CammyOS\n')
                self.assertEqual(view()['stage'], 2)
                servers = [named_process(name) for name in ('hostapd', 'udhcpd')]
                try:
                    self.assertEqual(view()['stage'], 4)
                finally:
                    for proc in servers:
                        proc.kill()
                        proc.wait()
                (run / 'state').write_text('HOME_STATION\n')
                (run / 'media-prepare/state').write_text('queued\n')
                v = view()
                self.assertEqual((v['stage'], v['media'], v['note']), (4, 'queued', 'Camera media is preparing.'))
                self.assertGreater(v['progress'], wifi_progress)
                self.assertLess(v['progress'], 100)
                # Another boot's guard, a link, or extra lines never count as this boot.
                (cfg / 'startup.pending').write_text('phase=idle_ready\nboot_id=bbbb-boot\n')
                v = view()
                self.assertEqual((v['guard'], v['stage']), (2, 0))
                (cfg / 'startup.pending').unlink()
                (cfg / 'real').write_text('phase=idle_ready\nboot_id=aaaa-boot\n')
                (cfg / 'startup.pending').symlink_to(cfg / 'real')
                self.assertEqual(view()['guard'], 2)
                (cfg / 'startup.pending').unlink()
                release = 'a' * 24
                (run / 'native-release.conf').write_text(f'release_id={release}\n')
                ready = run / 'release-health/display-ready'
                def acknowledge(boot, release_id=release, network='HOME_STATION'):
                    ready.write_text(f'release_id={release_id}\nboot={boot}\nnetwork={network}\n')
                acknowledge('bbbb-boot')
                self.assertEqual(view()['stage'], 0)
                acknowledge('aaaa-boot', 'b' * 24)
                self.assertEqual(view()['stage'], 0)
                acknowledge('aaaa-boot', network='AP_CammyOS')
                self.assertEqual(view()['stage'], 0)
                ready.write_text('boot=aaaa-boot\n')
                self.assertEqual(view()['stage'], 0)
                acknowledge('aaaa-boot')
                self.assertEqual(view()['stage'], 4)  # the queued media job is still pending
                (run / 'media-prepare/state').write_text('ready\n')
                (cfg / 'startup.pending').write_text('phase=idle_ready\nboot_id=aaaa-boot\n')
                self.assertEqual((view()['stage'], view()['progress']), (5, 100))
                # The release-bound idle latch RC06 startup writes opens the menu too, not
                # only once health clears the guard 120 s later (2026-11-24, boot c3f3e01d).
                (cfg / 'startup.pending').write_text(
                    f'phase=idle_ready\nboot_id=aaaa-boot\nrelease_id={release}\ncandidate_md5={"c" * 32}\n')
                self.assertEqual((view()['guard'], view()['stage']), (1, 5))
                (cfg / 'startup.pending').write_text(f'phase=idle_ready\nboot_id=aaaa-boot\nrelease_id={release}\n')
                self.assertEqual((view()['guard'], view()['stage']), (2, 4))
                (cfg / 'startup.pending').write_text('phase=idle_ready\nboot_id=aaaa-boot\n')
                # A stale fault record is ignored; this boot's fault fails the gate first.
                fault = 'format=cammy-runtime-health-fault/1\nboot_id=%s\nrelease_id=r\nuptime_s=90\nreason=gui-owner\nmedia_reason=none\n'
                (cfg / 'runtime-health-fault').write_text(fault % 'bbbb-boot')
                self.assertEqual(view()['stage'], 5)
                (cfg / 'runtime-health-fault').write_text(fault % 'aaaa-boot')
                v = view()
                self.assertEqual((v['fault'], v['stage'], v['view_reason']), (1, 6, 'gui-owner'))
            finally:
                for proc in (portal, other):
                    proc.kill()
                    proc.wait()



class UiTickTests(unittest.TestCase):
    """0.9 loop pacing (native/ui/ui_tick.h): 1 s while the menu is closed and idle."""
    def test_idle_tick_and_fast_tick_conditions(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'tick.c'; exe = Path(tmp) / 'tick'
            src.write_text(r'''#include <stdio.h>
#include "ui_tick.h"
int main(void) {
    struct ui_tick_in idle = {0, 0, 0, 0, 0, 0, 1, 5000, 0};
    printf("idle %u\n", ui_tick_ms(&idle));
    struct ui_tick_in t = idle; t.touch_wake = 0; printf("no-touch %u\n", ui_tick_ms(&t));
    const char *names[] = {"menu", "loading", "keys", "record", "publish", "volume"};
    for (int i = 0; i < 6; i++) { t = idle; ((int *)&t.menu_open)[i] = 1; printf("%s %u\n", names[i], ui_tick_ms(&t)); }
    t = idle; t.fast_until = 5001; printf("recent-input %u\n", ui_tick_ms(&t));
    t = idle; t.fast_until = 5000; printf("input-expired %u\n", ui_tick_ms(&t));
    printf("stale %u hold %u\n", CUI_STALE_MS, UI_INPUT_HOLD_MS);
    return 0;
}
''')
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'native/ui'), str(src), '-o', str(exe)], check=True)
            got = dict(line.split(' ', 1) for line in subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.splitlines())
        self.assertEqual(got['idle'], '1000')
        self.assertEqual(got['no-touch'], '250')
        for fast in ('menu', 'loading', 'keys', 'record', 'publish', 'volume', 'recent-input'):
            self.assertEqual(got[fast], '100', fast)
        self.assertEqual(got['input-expired'], '1000')
        self.assertEqual(got['stale'], '3000 hold 2000')  # the face side drops the menu after 3 s without a heartbeat

    def test_loop_polls_only_devices_it_reads(self):
        """A stock-mode button fd left unread kept poll returning at once; the touch wake is idle-only."""
        loop = (ROOT / 'native/ui/cammy_ui.c').read_text()
        self.assertIn('if (keys_read) for (unsigned n = 0; n < BTN_DEVS; n++) if (btn.d[n].fd >= 0)', loop)
        self.assertIn('if (rec_read && rec.fd >= 0)', loop)
        self.assertIn('if (wait != UI_TICK_FAST_MS && touch) {', loop)
        self.assertIn('int touch = cammy && touch_open(', loop)
        self.assertNotIn('EVIOCGRAB, 1', loop.split('static int touch_open', 1)[1].split('static void record_poll', 1)[0])


if __name__ == '__main__':
    unittest.main()
