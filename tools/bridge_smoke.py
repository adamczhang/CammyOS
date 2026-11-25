"""Exact-library RAM-only SYNTHETIC host smoke. No candidate/app/boot actions.

Run local first, then device with that receipt. Only device mutations are exclusive
0700 /tmp directories, two exact-hash files, owned result/PID records and cleanup.
The fixed library socket namespace must be absent. No existing path is reclaimed.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from c3 import discover  # noqa: E402
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import struct
import subprocess
import sys
import time
import urllib.request
import uuid

from tools.bootstrap_readiness import AdbReader

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'private/bridge-smoke'
HOST = ROOT / 'build/bridge-smoke/smoke_host'
LIB = ROOT / 'private/ownership-program/wp2a/library/libcammy_native_status.so'
ROOTFS = ROOT / 'build/native-loader/device-rootfs'
DEPS = ROOT.parent / 'Cammy Bot/tools/build-deps/usr'
BB = '/tmp/cammy-runtime/busybox'
SOCKET_DIR = '/tmp/cammy-bridge'
LIB_SHA = 'db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065'
PINNED = {
 '/etc/init.d/S01app': '3327c3a919b8863afd16ad9cced159634e2870ae6b07333bccd4d0a921a72136',
 '/usr/bin/sdvcam': '8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec',
 '/lib/libc.so': '5fea26c4e562809794d33fd9c62304cef68b2db6233a8372bedd31e0baac7f51',
 BB: '064af12020501cc0ecfefb497a1f38633adc241a5ee902b87ce2f3582acb43d4',
}
HASH_PATHS = [*PINNED, '/etc/inittab', '/etc/init.d/rcS', '/etc/init.d/rc.final']
ANSI = re.compile(r'\x1b\[[0-9;]*m')

def sha(blob):
    return hashlib.sha256(blob).hexdigest()

def mounted():
    work = os.environ.get('CAMMY_WORK_MOUNT')
    if not work:
        return
    p = subprocess.run(['findmnt', '-n', '-o', 'FSTYPE', work],
                       capture_output=True, text=True, timeout=5, check=True)
    if p.stdout.strip() != 'ext4':
        raise ValueError('LAB ext4 is not mounted')

def temperatures():
    result = {}
    for p in Path('/sys/class/hwmon').glob('hwmon*'):
        if (p/'name').read_text().strip() not in ('nvme', 'drivetemp'):
            continue
        key = p.resolve().parent.name
        result[key] = {t.name: int(t.read_text())/1000 for t in p.glob('temp*_input')}
    if any(t >= 81 for drive in result.values() for t in drive.values()):
        raise ValueError('drive temperature >=81C: ' + json.dumps(result))
    return result or {'unavailable': True}

def save(path, value):
    mounted()
    path.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')

def workdir(kind):
    mounted()
    out = OUT / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+kind+'-'+secrets.token_hex(3))
    out.mkdir(parents=True, mode=0o700)
    return out

def proc_identity(line):
    m = re.fullmatch(r'(\d+) \(([^\n]*)\) (.*)', line.strip())
    if not m or len(m[3].split()) < 20 or not m[3].split()[19].isdigit():
        raise ValueError('invalid process stat')
    return {'pid': int(m[1]), 'comm': m[2], 'starttime': m[3].split()[19]}

def ram_path(path):
    if not re.fullmatch(r'/tmp/cammy-smoke-[0-9a-f]{24}', path):
        raise ValueError('exclusive RAM path required')
    return path

def owned_directory(text, path):
    # ls -ldin: inode, mode, nlink, numeric uid/gid, size, date, path.
    lines = ANSI.sub('', text).strip().splitlines()
    if len(lines) != 1:
        raise ValueError('expected one directory record')
    fields = lines[0].split()
    if (len(fields) < 8 or not fields[0].isdigit() or fields[1] != 'drwx------'
            or fields[3:5] != ['0', '0'] or fields[-1] != path):
        raise ValueError('directory is not private root-owned RAM')
    return {'inode': fields[0], 'mode': fields[1], 'uid': fields[3], 'gid': fields[4], 'path': path}

def validate_responses(events, expected_boot=None):
    frames = [e for e in events if e.get('event') == 'response']
    if [e['command'] for e in frames] != [1, 2, 3]:
        raise ValueError('exactly hello/status/capabilities required')
    identity = None
    decoded = []
    for cmd, event in enumerate(frames, 1):
        b = bytes.fromhex(event['hex'])
        if len(b) != (112 if cmd == 1 else 128):
            raise ValueError('response length')
        magic, major, minor, size, command, rid, status, backend, payload = struct.unpack_from('<4sHHHHIHHI', b)
        instance = struct.unpack_from('<Q', b, 24)[0]
        boot = uuid.UUID(bytes=b[32:48]).hex
        now, owner = struct.unpack_from('<QQ', b, 48)
        if (magic, major, minor, size, command, rid, status, backend, payload) != (
                b'CBRS', 1, 0, 64, cmd, cmd, 0, 2, len(b)-64) or not instance or not now or owner:
            raise ValueError('response header')
        if expected_boot and boot != uuid.UUID(expected_boot).hex:
            raise ValueError('response boot mismatch')
        if identity is not None and identity != (instance, boot):
            raise ValueError('response instance/boot changed')
        identity = (instance, boot)
        p = b[64:]
        if cmd == 1 and struct.unpack_from('<HHHH6I', p) != (1, 0, 4, 4, 64, 256, 10000, 60000, 14, 8):
            raise ValueError('hello payload')
        if cmd == 2 and struct.unpack_from('<4H', p) != (2, 1, 0, 1):
            raise ValueError('status must be ready, no hardware or ownership flags')
        if cmd == 3:
            if struct.unpack_from('<HHI', p) != (7, 8, 0):
                raise ValueError('capability count/layout')
            for i in range(7):
                actual = struct.unpack_from('<HBBB3s', p, 8+i*8)
                wanted = (i+1, 1 if i == 0 else 0, 0 if i == 0 else 3, 0 if i == 0 else 1, b'\0'*3)
                if actual != wanted:
                    raise ValueError('physical capability must remain unavailable')
        decoded.append({'command': cmd, 'instance': instance, 'boot': boot, 'mono_ms': now})
    return decoded

def validate_events(events, library, boot=None):
    ids = [e for e in events if e.get('event') == 'identity']
    if len(ids) != 1 or (ids[0]['caller'], ids[0]['time_t'], ids[0]['stat_size']) != ('SYNTHETIC', 4, 104):
        raise ValueError('synthetic time32 identity required')
    if not any(e.get('event') == 'map' and e['line'].endswith(' '+library) for e in events):
        raise ValueError('exact staged library was not mapped')
    a = [e for e in events if e.get('event') == 'attach']
    d = [e for e in events if e.get('event') == 'detach']
    serving = [e for e in events if e.get('event') == 'serving']
    if len(a) != 1 or a[0]['rc'] or len(d) != 1 or not d[0]['pass']:
        raise ValueError('attach/detach failure')
    if len(serving) != 1 or serving[0]['non_cloexec']:
        raise ValueError('serving descriptor check failed')
    if (not d[0]['socket_absent'] or not d[0]['fd_set_equal'] or d[0]['fds_before'] != d[0]['fds_after']
            or d[0]['tasks_before'] != d[0]['tasks_after'] or serving[0]['tasks'] != d[0]['tasks_before']+1):
        raise ValueError('lifecycle leak/abandon')
    return {'responses': validate_responses(events, boot), 'timing_us': {
        'load': ids[0]['load_us'], 'attach': a[0]['us'], 'detach': d[0]['us']}, 'caller': 'SYNTHETIC'}

def audit_host():
    sys.path.insert(0, str(ROOT/'native/loader'))
    from elfinfo import Elf32, PT_INTERP, PT_GNU_STACK
    e = Elf32(HOST.read_bytes()); libc = Elf32((ROOTFS/'lib/libc.so').read_bytes())
    imports = e.undefined()
    interp = [e.data[p['offset']:p['offset']+p['filesz']].rstrip(b'\0').decode() for p in e.phdrs if p['type'] == PT_INTERP]
    forbidden = {'kill', 'raise', 'signal', 'sigaction', 'ioctl', 'system', 'popen', 'fork', 'execve', 'dlclose', 'mount', 'reboot'}
    if ((e.e_type, e.e_machine, e.e_flags) != (2, 40, 0x5000400) or e.needed() != ['libc.so']
            or interp != ['/lib/ld-musl-armhf.so.1'] or set(imports)-libc.exported()
            or set(imports) & forbidden or any('time64' in s for s in imports)
            or not any(p['type'] == PT_GNU_STACK and not p['flags'] & 1 for p in e.phdrs)
            or any(t in (15, 29, 22) for t, _ in e.dynamic())):
        raise ValueError('smoke host ABI/import audit failed')
    return {'sha256': sha(HOST.read_bytes()), 'imports': imports, 'interpreter': interp, 'needed': e.needed(), 'pass': True}

def local():
    out = workdir('local'); temps = temperatures()
    subprocess.run(['make', '-C', 'native/smoke', 'smoke'], cwd=ROOT, check=True, timeout=60,
                   stdout=(out/'build.log').open('w'), stderr=subprocess.STDOUT)
    if sha(LIB.read_bytes()) != LIB_SHA:
        raise ValueError('frozen release library mismatch')
    subprocess.run([sys.executable, 'native/loader/abi_audit.py', '--library', str(LIB), '--rootfs', str(ROOTFS),
                    '--output', str(out/'library-abi.json')], cwd=ROOT, check=True, timeout=20)
    host = audit_host(); runs = {}
    for scenario in ('success', 'reject-hash'):
        tmp = out/('tmp-'+scenario); (tmp/'cammy-bridge').mkdir(parents=True, mode=0o700)
        args = ['unshare', '-rm', 'sh', '-c', 'mount --bind "$1" /tmp && shift && exec "$@"', 'sh', str(tmp),
                str(DEPS/'bin/qemu-arm'), '-strace', '-L', str(ROOTFS), str(HOST), str(LIB)]
        if scenario != 'success': args.append(scenario)
        result = subprocess.run(args, capture_output=True, timeout=10, cwd=ROOT)
        (out/(scenario+'.jsonl')).write_bytes(result.stdout)
        (out/(scenario+'.strace')).write_bytes(result.stderr)
        events = [json.loads(s) for s in result.stdout.splitlines()]
        if result.returncode: raise ValueError('qemu host failed: '+scenario)
        if scenario == 'success': runs[scenario] = validate_events(events, str(LIB))
        else:
            if not any(e.get('event') == 'attach' and e['rc'] == -71 for e in events) or events[-1] != {'event':'refused', 'clean':True}:
                raise ValueError('qemu identity refusal failed')
            runs[scenario] = {'identity_rejected': True, 'clean': True}
    report = {'kind':'LOCAL_QEMU_SYNTHETIC', 'host':host, 'library_sha256':LIB_SHA, 'runs':runs, 'pass':True, 'temperatures_c':temps}
    save(out/'local.json', report)
    save(OUT/'current.json', {'phase':'local_passed', 'local_report':str(out/'local.json'), 'next':'Device preflight and one RAM-only success-path run'})
    print(out/'local.json')
    return report

SNAPSHOT = r'''printf 'BOOT='; cat /proc/sys/kernel/random/boot_id
printf 'KERNEL='; cat /proc/version
printf 'CMDLINE='; cat /proc/cmdline
printf '\n--ROOT--\n'; cat /proc/self/status
printf '\n--MOUNTS--\n'; cat /proc/mounts
printf '\n--TMP--\n'; ls -ldin /tmp
printf '\n--HASHES--\n'; /tmp/cammy-runtime/busybox sha256sum HASH_PATHS
printf '\n--NATIVE--\n'
for f in /proc/[0-9]*/comm; do
 read -r name <"$f" || continue
 if [ "$name" = sdvcam ]; then
  pid=${f#/proc/}; pid=${pid%/comm}
  printf 'STAT='; cat /proc/$pid/stat
  printf 'EXE='; readlink /proc/$pid/exe
  printf 'WD='; readlink /proc/$pid/fd/3
  printf 'WDINFO='; cat /proc/$pid/fdinfo/3
  printf 'WDNODE='; ls -lin /proc/$pid/fd/3
  printf '\n--NATIVE-MAPS--\n'; cat /proc/$pid/maps
 fi
done
printf '\n--SELECT--\n'
for p in /run/cammy-select /etc/cammy/native-select.enabled /etc/cammy/native-select.arm /data/app_md5; do
 if [ -e "$p" ] || [ -L "$p" ]; then printf 'PRESENT:%s\n' "$p"; else printf 'ABSENT:%s\n' "$p"; fi
done
printf '\n--END--\n'
'''.replace('HASH_PATHS', ' '.join(HASH_PATHS))

def snapshot(reader):
    raw = ANSI.sub('', reader.shell(SNAPSHOT).decode())
    def field(name):
        m = re.search(r'^'+name+r'=(.*)$', raw, re.M)
        if not m: raise ValueError('missing identity '+name)
        return m[1]
    boot = str(uuid.UUID(field('BOOT')))
    hashes = {p:h for h,p in re.findall(r'^([0-9a-f]{64})  (/[\S]+)$',raw,re.M)}
    statlines = re.findall(r'^STAT=(.*)$', raw, re.M)
    if len(statlines) != 1 or field('EXE') != '/usr/bin/sdvcam' or field('WD') != '/dev/watchdog':
        raise ValueError('stock native/watchdog owner mismatch')
    if any(hashes.get(p) != h for p,h in PINNED.items()) or set(hashes) != set(HASH_PATHS):
        raise ValueError('stock/bootstrap/libc/hash-reader identity mismatch')
    root = raw.split('--ROOT--\n')[1].split('--MOUNTS--')[0]
    if not re.search(r'^Uid:\s+0\s+0\s+0\s+0$',root,re.M) or not re.search(r'^Gid:\s+0\s+0\s+0\s+0$',root,re.M):
        raise ValueError('root ADB required')
    mounts = raw.split('--MOUNTS--\n')[1].split('--TMP--')[0].strip().splitlines()
    tmp = [s.split() for s in mounts if s.split()[1] == '/tmp']
    if (len(tmp) != 1 or tmp[0][2] != 'tmpfs' or 'noexec' in tmp[0][3].split(',')
            or any(s.split()[1].startswith('/tmp/') for s in mounts)):
        raise ValueError('RAM confinement: executable tmpfs /tmp with no submounts required')
    tmpmeta = raw.split('--TMP--\n')[1].split('--HASHES--')[0].strip().split()
    if len(tmpmeta) < 8 or tmpmeta[1] != 'drwxrwxrwt' or tmpmeta[3:5] != ['0','0'] or tmpmeta[-1] != '/tmp':
        raise ValueError('untrusted /tmp ancestor')
    maps = raw.split('--NATIVE-MAPS--\n')[1].split('--SELECT--')[0].strip()
    if 'libcammy_native_status.so' in maps or 'cammy-smoke-' in maps:
        raise ValueError('test library unexpectedly mapped in native app')
    selection = raw.split('--SELECT--\n')[1].split('--END--')[0].strip()
    if 'PRESENT:' in selection:
        raise ValueError('unexpected candidate/app selection state')
    return {'boot':boot, 'native':proc_identity(statlines[0]), 'hashes':hashes, 'kernel':field('KERNEL'),
            'cmdline':field('CMDLINE'), 'wd_info':raw.split('WDINFO=')[1].split('--NATIVE-MAPS--')[0].strip(),
            'mounts':mounts, 'native_maps':maps, 'selection':selection, 'raw':raw}

def unchanged(before, after):
    for key in ('boot','native','hashes','kernel','cmdline','wd_info','mounts','native_maps','selection'):
        if before[key] != after[key]: raise ValueError('unexpected stock state change: '+key)

def gateway():
    config = json.loads((ROOT/'private/host/config.json').read_text())
    token = Path(config['token_file']).read_text().strip()
    req = urllib.request.Request('http://127.0.0.1:8790/health',headers={'Authorization':'Bearer '+token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req,timeout=4) as reply: data=json.loads(reply.read(65536))
    result={k:data.get(k) for k in ('connected','endpoint','identity','sensing_started_by_gateway','offline','simulation')}
    for path,keys in [('/v1/state',('owner','preview_active','sensing_default')),
                      ('/v1/preview/status',('phase','preview_active','event_connected'))]:
        request=urllib.request.Request('http://127.0.0.1:8790'+path,headers={'Authorization':'Bearer '+token})
        with opener.open(request,timeout=4) as reply: status=json.loads(reply.read(65536))
        result[path]={k:status[k] for k in keys}
    # No token, config, event histories or full response retention.
    return result

def validate_gateway(g):
    if (g.get('connected') is not True or g.get('endpoint') != discover.portal()
            or g.get('sensing_started_by_gateway') is not False or g.get('offline') is not False
            or g.get('simulation') is not False or g.get('identity') != {
                'os':'CammyOS','portal_version':'0.3.0','image_marker_present':False,
                'profile':'m105-stock-fork','kernel_replaced':False}):
        raise ValueError('gateway must remain real/idle/connected on home endpoint')
    if (g.get('/v1/state') != {'owner':None,'preview_active':False,'sensing_default':'off'}
            or g.get('/v1/preview/status') != {'phase':'idle','preview_active':False,'event_connected':False}):
        raise ValueError('gateway owner/preview/sensing must be idle')

class Trial:
    def __init__(self, reader, out, ram):
        self.reader=reader; self.out=out; self.ram=ram_path(ram)
        self.token=secrets.token_hex(24); self.owned={}
    def journal(self, **extra):
        save(self.out/'ownership.json', {'ram':self.ram,'token':self.token,'owned':self.owned,**extra})
    def inspect_dir(self, path):
        meta=owned_directory(self.reader.shell('ls -ldin '+path).decode(),path)
        if meta != self.owned[path] or self.reader.shell('cat '+path+'/.owner').decode().strip() != self.token:
            raise ValueError('cleanup ownership changed')
    def create(self, path):
        if path not in (self.ram, SOCKET_DIR): raise ValueError('write outside owned RAM')
        # Exclusive mkdir; no mkdir -p, symlink following, overwrite or stale reclamation.
        command=f"umask 077; [ ! -e {path} ] && [ ! -L {path} ] && mkdir {path} && chmod 700 {path} && printf '%s\\n' {self.token} > {path}/.owner && ls -ldin {path}"
        meta=owned_directory(self.reader.shell(command).decode(),path)
        self.owned[path]=meta; self.journal()
        self.inspect_dir(path)
    def push(self, src, name):
        if name not in ('smoke_host','libcammy_native_status.so'): raise ValueError('unexpected payload')
        self.inspect_dir(self.ram)
        dest=self.ram+'/'+name
        result=subprocess.run(self.reader.base+['push',str(src),dest],capture_output=True,timeout=15)
        if result.returncode: raise ValueError('ADB push failed')
        digest=self.reader.shell(BB+' sha256sum '+dest).decode().split()[0]
        if digest != sha(src.read_bytes()): raise ValueError('staged payload hash mismatch')
        return digest
    def stop_owned_if_running(self, boot, host_sha):
        if self.ram not in self.owned: return
        self.inspect_dir(self.ram)
        raw=self.reader.shell('cat '+self.ram+'/pid '+self.ram+'/start 2>/dev/null').decode().splitlines()
        if not raw: return
        if len(raw) != 2 or not raw[0].isdigit(): raise ValueError('no verified cleanup PID/start record')
        ident=proc_identity(raw[1]); pid=int(raw[0])
        if pid != ident['pid'] or pid <= 1 or not boot: raise ValueError('cleanup identity incomplete')
        live=self.reader.shell(f'if [ -e /proc/{pid} ]; then cat /proc/{pid}/stat; fi').decode().strip()
        if not live: return
        now=proc_identity(live)
        if now['starttime'] != ident['starttime']: return  # original process exited; PID reused
        # Read-only gates first; repeat boot/exe/starttime immediately at the sole kill.
        if self.reader.shell(f'{BB} sha256sum /proc/{pid}/exe').decode().split()[0] != host_sha:
            raise ValueError('cleanup executable hash changed')
        command=(f'[ "$(cat /proc/sys/kernel/random/boot_id)" = "{str(uuid.UUID(boot))}" ] && '
                 f'[ "$(readlink /proc/{pid}/exe)" = "{self.ram}/smoke_host" ] && '
                 f'''[ "$(awk '{{print $22}}' /proc/{pid}/stat)" = "{ident["starttime"]}" ] && '''
                 f'kill -15 {pid} && printf "OWNED_TERM_SENT\\n"')
        if self.reader.shell(command).decode().strip() != 'OWNED_TERM_SENT':
            raise ValueError('cleanup signal identity gate refused')
        self.journal(emergency_term={'pid':pid,'starttime':ident['starttime'],'boot':boot})
        # No forced-crash escalation. cleanup itself refuses any remaining host.
        time.sleep(0.1)
    def cleanup(self):
        # Never remove directories while any executable from this trial still runs.
        found=self.reader.shell(f'for p in /proc/[0-9]*/exe; do x=$(readlink "$p"); if [ "$x" = {self.ram}/smoke_host ]; then printf "LIVE:%s\\n" "$p"; fi; done').decode()
        if 'LIVE:' in found: raise ValueError('owned host still running; preserve RAM for identity-gated cleanup')
        evidence={}
        for path in reversed(list(self.owned)):
            self.inspect_dir(path)
            names=ANSI.sub('',self.reader.shell('ls -A1 '+path).decode()).splitlines()
            allowed={'.owner','bridge.sock.lock'} if path == SOCKET_DIR else {'.owner','smoke_host','libcammy_native_status.so','result.jsonl','pid','start'}
            if set(names)-allowed: raise ValueError('unexpected RAM member; refuse recursive cleanup')
            self.inspect_dir(path)
            raw=self.reader.shell(f'rm -r {path}; if [ ! -e {path} ] && [ ! -L {path} ]; then printf "REMOVED\\n"; fi').decode()
            if raw.strip() != 'REMOVED': raise ValueError('cleanup failed')
            evidence[path]={'identity':self.owned[path], 'members':names,'absent':True}
            del self.owned[path]
        self.journal(cleanup=evidence)
        return evidence

def launch_command(ram):
    ram_path(ram)
    return (f'umask 077; if ! ulimit -c 0; then printf "SMOKE_CORE_LIMIT_REFUSED\\n"; exit 1; fi; '
            f'{ram}/smoke_host {ram}/libcammy_native_status.so > {ram}/result.jsonl 2>&1 & '
            f'p=$!; printf "%s\\n" "$p" > {ram}/pid; cat /proc/$p/stat > {ram}/start; '
            f'wait "$p"; rc=$?; printf "SMOKE_EXIT=%s\\n" "$rc"')

def device(local_report):
    receipt=json.loads(local_report.read_text())
    if not receipt.get('pass') or receipt.get('kind') != 'LOCAL_QEMU_SYNTHETIC' or receipt['host'] != audit_host() or receipt['library_sha256'] != sha(LIB.read_bytes()) or sha(LIB.read_bytes()) != LIB_SHA:
        raise ValueError('fresh matching local ABI/qemu receipt required')
    out=workdir('device'); ram='/tmp/cammy-smoke-'+secrets.token_hex(12)
    envdirs=[str(DEPS/'lib/x86_64-linux-gnu/android'),str(DEPS/'lib/x86_64-linux-gnu')]
    os.environ['LD_LIBRARY_PATH']=':'.join(envdirs)
    reader=AdbReader(DEPS/'lib/android-sdk/platform-tools/adb','20080411',5038)
    trial=Trial(reader,out,ram)
    status={'phase':'preflight','kind':'REAL_M105_LIBRARY_SYNTHETIC_HOST','out':str(out),'ram':ram,'local_report':str(local_report),'temperatures_c':temperatures()}
    def checkpoint(**extra):
        status.update(extra); save(out/'result.json',status); save(OUT/'current.json',status)
    checkpoint()
    try:
        before=snapshot(reader); save(out/'before.json',before)
        prior=json.loads((ROOT/'private/bootstrap-hardware-test/current.json').read_text())
        if before['boot'] != prior['restored_boot_id'] or str(before['native']['pid']) != prior['restored_native_pid']:
            raise ValueError('bootstrap boot/native identity changed')
        g0=gateway(); save(out/'gateway-before.json',g0)
        validate_gateway(g0)
        checkpoint(phase='staging', baseline={'boot':before['boot'],'native':before['native']})
        trial.create(ram); trial.create(SOCKET_DIR)
        hashes={n:trial.push(p,n) for n,p in [('smoke_host',HOST),('libcammy_native_status.so',LIB)]}
        if hashes != {'smoke_host':receipt['host']['sha256'], 'libcammy_native_status.so':LIB_SHA}:
            raise ValueError('staged payload differs from offline-tested bytes')
        reader.shell('chmod 500 '+ram+'/smoke_host; chmod 400 '+ram+'/libcammy_native_status.so')
        trial.inspect_dir(ram); trial.inspect_dir(SOCKET_DIR)
        unchanged(before,snapshot(reader)); checkpoint(phase='running',staged_sha256=hashes)
        # No startup script. A bounded private host, child-only core suppression.
        command=launch_command(ram)
        t=time.monotonic(); raw=reader.shell(command).decode(); elapsed=time.monotonic()-t
        (out/'exit.txt').write_text(raw)
        output=reader.shell('cat '+ram+'/result.jsonl').decode(); (out/'hardware.jsonl').write_text(output)
        start=reader.shell('cat '+ram+'/start').decode().strip(); pid=reader.shell('cat '+ram+'/pid').decode().strip()
        ident=proc_identity(start)
        if ident['pid'] != int(pid): raise ValueError('test process identity inconsistent')
        gone=reader.shell(f'if [ ! -e /proc/{int(pid)} ]; then printf "EXITED\\n"; else cat /proc/{int(pid)}/stat; fi').decode().strip()
        if gone != 'EXITED': raise ValueError('test process still exists; retain identity evidence')
        checkpoint(phase='validating',host_identity=ident,host_exited=True,wall_seconds=elapsed)
        after=snapshot(reader); save(out/'after.json',after); unchanged(before,after)
        events=[json.loads(line) for line in output.splitlines()]
        summary=validate_events(events,ram+'/libcammy_native_status.so',before['boot'])
        if raw.strip() != 'SMOKE_EXIT=0': raise ValueError('host exit marker failed')
        cleanup=trial.cleanup(); g1=gateway(); save(out/'gateway-after.json',g1)
        if g1 != g0: raise ValueError('gateway identity/state changed')
        unchanged(before,snapshot(reader))
        checkpoint(phase='PASS_RAM_LIBRARY_SYNTHETIC_HOST_ONLY',summary=summary,cleanup=cleanup,gateway_preserved=True,
                   stock_preserved=True,temperatures_after_c=temperatures(),candidate_activation_allowed=False)
    except Exception as error:
        checkpoint(phase='STOPPED',error=type(error).__name__+': '+str(error))
        # Only remove verified owned paths, and only once the private host is absent.
        if trial.owned:
            try:
                trial.stop_owned_if_running(status.get('baseline',{}).get('boot'), receipt['host']['sha256'])
                checkpoint(cleanup=trial.cleanup())
            except Exception as cleanup_error: checkpoint(cleanup_error=str(cleanup_error))
        raise
    print(out/'result.json')
    return status

def main():
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='mode',required=True)
    sub.add_parser('local'); d=sub.add_parser('device'); d.add_argument('--local-report',type=Path,required=True)
    a=p.parse_args()
    if a.mode == 'local': local()
    else: device(a.local_report)

if __name__ == '__main__': main()
