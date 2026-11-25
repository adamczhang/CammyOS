"""Build an inert, credential-free integrated local CammyOS candidate.

Never connects to a camera, writes a card, installs init, arms or reboots.
"""
import argparse,hashlib,json,os,re,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tools'))
from tools import build_native_face_candidate as F, cammy_pack, rc_wifi_policy
from tools.build_face_launch_review import BASE_SHA
from boot_launch.generator import generate_proposal,SAVED_STOCK_S01APP,HELPER_PROPOSAL_PATH
from native_trial import records as R
VERSION='0.9.12'
FACE_LIBRARY_BUDGET=10*1024*1024  # bytes of camera RAM (tmpfs copy)
FACE_DECODE_BYTES=(4+4+3)*cammy_pack.W*cammy_pack.H+8  # two canvas buffers plus BGR key and index
CLASS='gui-face-service-v1'
GATE='rc06-native-authorized'
BASE_RUNTIME_SHA='a53f276c424de17051334f7445170573ace6cb9cc34d337435750c0a1d9eb96f'
def sha(b):return hashlib.sha256(b).hexdigest()
def md5(b):return hashlib.md5(b).hexdigest()
# Native sources build() compiles itself or through make, each with the -I folders
# of its compile. Their quoted #include closure goes into the receipt, so an included
# header or .c (native/speaker_probe/speaker_stream.h, native/face_service/*.c via
# src/cammy_face_control.h) cannot drop out of it.
UI_INCLUDES=('native/ui','native/face_service')
DESKTOP_MAINTENANCE_INPUTS=(
    'docs/CAMMY-BRAIN-FOUNDATION.md','tools/cammy_lifecycle.py','tools/lifecycle_usb.py',
    'tools/rc_install.py','tools/rc_wifi_policy.py','tools/native_trial/__init__.py',
    'tools/native_trial/policy.py',
)
NATIVE_ROOTS={'src/boot/fault_evidence.c':(),'src/cammy_portal.c':(),'src/boot/native_service_client.c':(),'src/compat/arm_legacy_stat.c':(),
    'native/speaker_probe/guardian.c':(),'native/face_service/native_service.c':('build/face-service',),'native/face_service/face_service.c':(),
    'native/face_service/ipc_file.c':(),'native/face_service/status_main.c':(),'native/face/face_stub.S':(),
    'native/ui/cammy_ui.c':UI_INCLUDES,'native/ui/ui_backend.c':UI_INCLUDES,'native/ui/ui_app.c':UI_INCLUDES,'native/ui/ui_draw.c':UI_INCLUDES}
# Build graph and Python modules build() runs or imports (tools/rc06_build.py itself is
# the seed's 'builder'), plus the prebuilt object linked into every static ARM binary.
BUILD_INPUTS=['native/face_service/Makefile','native/face/exports.map','build/arm/arm_legacy_stat.o',
    'tools/cammy_pack.py','assets/faces/cammy-adult-atlas.png','assets/faces/cammy-adult-sleep.png',
    'tools/c3/cammy-calendar-broker.service',
    'native/activation/launch_selected_or_stock.proposal','tools/build_native_face_candidate.py','tools/build_native_status_candidate.py',
    'tools/display_owner_audit.py','tools/build_face_launch_review.py','tools/boot_launch/__init__.py','tools/boot_launch/generator.py',
    'tools/boot_launch/model.py','tools/native_trial/__init__.py','tools/native_trial/policy.py','tools/native_trial/records.py',
    'tools/shared_capture_profile.py','tools/build_ui_assets.py','tools/rc_runtime_integrity.py','tools/native_image_abi.py','native/loader/abi_audit.py','native/loader/elfinfo.py']
INCLUDE=re.compile(rb'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"',re.M)
def native_closure(root=ROOT):
    """Every repository file reached from NATIVE_ROOTS by quoted #include; fails closed."""
    seen=set();todo=[(root/name,tuple(root/d for d in dirs)) for name,dirs in NATIVE_ROOTS.items()]
    while todo:
        path,dirs=todo.pop()
        if path in seen:continue
        seen.add(path)
        for name in INCLUDE.findall(path.read_bytes()):
            name=name.decode()
            found=next((c for c in [path.parent/name,*(d/name for d in dirs)] if c.is_file()),None)
            if found is None:raise ValueError(f'Unresolved native include {name} in {path.relative_to(root)}')
            found=found.resolve(strict=True)
            if not found.is_relative_to(root):raise ValueError(f'Native include outside the project: {name}')
            todo.append((found,dirs))
    return seen
def source_identities(root=ROOT):
    root=Path(root).resolve()
    paths=set(native_closure(root))
    for folder in ['host','sdk','scripts/rc06','assets/controls','native/ui']:
        paths.update(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and (p.suffix in ('.py','.sh','.html','.js','.css','.toml','.json','.c','.h') or p.name=='Makefile'))
    paths.update(root/p for p in BUILD_INPUTS)
    paths.update(root/p for p in DESKTOP_MAINTENANCE_INPUTS)
    paths.update(root/p for p in ['docs/RELEASE-0.6.0-CANDIDATE.md','docs/RC06-INSTALL-REVIEW.md','docs/M105-CONTROL-INVENTORY.md','tools/rc06_validate.py','tools/rc_wifi_policy.py','tools/speaker_profile.py','tools/fin_fur_art.py','tools/build_face_service_assets.py','native/speaker_probe/guardian.c','native/speaker_probe/Makefile','src/cammy_speaker.h','docs/SPEAKER-ROUTING-2026-11-15.md','docs/RELEASE-0.6.0-RC2.md','scripts/home-dhcp.sh','scripts/shared-capture.sh','scripts/native-media-start.sh','scripts/S99cammy-tf','scripts/tf-runtime-startup.sh','build/arm/busybox-dhcp','build/arm/wpa_supplicant','build/arm/wpa_cli','build/arm/native-service-client','build/arm/daemon-exec','build/face-service/face-status','build/paramface/libcammy_paramface.so'])
    return {str(p.relative_to(root)):sha(p.read_bytes()) for p in sorted(paths)}
def replace_once(text,old,new):
    if text.count(old)!=1:raise ValueError('Source context differs: '+old[:70])
    return text.replace(old,new)

def helper(slot):
    raw=HELPER_PROPOSAL_PATH.read_bytes()
    if sha(raw)!=BASE_SHA:raise ValueError('Reviewed helper identity changed')
    s=raw.decode()
    for old,new in [('LIBROOT=/tmp/cammy-native-status\n','LIBROOT=/tmp/cammy-face\n'),('LIBNAME=libcammy_native_status.so\n','LIBNAME=face.so\n'),('status-only|media-lease-gated)',CLASS+'|media-lease-gated)'),('\t\tstatus-only) ;;','\t\t'+CLASS+') ;;'),('TFDIR="$LAUNCH_TFMNT/CammyOS-native"','TFDIR="$LAUNCH_TFMNT/'+slot+'/native"')]:s=replace_once(s,old,new)
    s=s.replace('chunk5-fixture-open',GATE)
    retry=(ROOT/'scripts/rc06/selector-retry.sh').read_text()
    s=replace_once(s,'cammy_prepare_one_shot() {',retry+'''
cammy_prepare_one_shot() {
	# Quarantine a spent recovery even if the fault records were already archived.
	if [ -e "$CFG/native-select.crash-used" ] || [ -L "$CFG/native-select.crash-used" ]; then
		if ! cammy_crash_ledger || [ "$pr_retry" != "$LAUNCH_BOOT" ] || ! cammy_crash_authority; then
			cammy_finish_prepare stock crash-retry-spent
			return 0
		fi
	fi
	if [ -e "$CFG/startup.pending" ] || [ -L "$CFG/startup.pending" ]; then
		if [ "$(cammy_get phase "$CFG/startup.pending")" = radio_fault ]; then
			cammy_radio_retry || { cammy_finish_prepare stock "$CAMMY_RETRY_REASON"; return 0; }
		elif [ "$(cammy_get phase "$CFG/startup.pending")" = post_healthy_fault ] ||
		   [ -n "$(cammy_get release_id "$CFG/startup.pending")" ]; then
			cammy_crash_retry || { cammy_finish_prepare stock "$CAMMY_RETRY_REASON"; return 0; }
		elif ! cammy_power_retry; then
			cammy_finish_prepare stock "$CAMMY_RETRY_REASON"
			return 0
		fi
	fi
	if [ -e "$CFG/runtime-health-fault" ] || [ -L "$CFG/runtime-health-fault" ]; then
		cammy_finish_prepare stock previous-runtime-fault
		return 0
	fi''')
    return s.encode()

def runtime(slot):
    raw=(ROOT/'scripts/tf-runtime-startup.sh').read_bytes()
    if sha(raw)!=BASE_RUNTIME_SHA:raise ValueError('Accepted startup source changed')
    s=raw.decode();s=replace_once(s,'CARD=/mnt/extsd/CammyOS','CARD=/mnt/extsd/'+slot+'/runtime')
    # Intermediate progress stays in RAM. The synced launch latch remains
    # authoritative until the final, release-bound idle latch replaces it.
    s=replace_once(s,r''' if [ -e "$GUARD" ]; then
  printf 'phase=%s\nboot_id=%s\n' "$1" "$BOOT_ID" >"$GUARD"
  sync
 fi
''',r''' printf '%s\n' "$1" >"$R/startup.phase"
 [ "$1" = idle_ready ] || return 0
 if [ -f "$GUARD" ] && [ ! -L "$GUARD" ]; then
  PHASE_TMP=/etc/cammy/.startup-phase.$$
  rm -f "$PHASE_TMP"
  if (set -C; printf 'phase=%s\nboot_id=%s\nrelease_id=%s\ncandidate_md5=%s\n' "$1" "$BOOT_ID" "$RELEASE_ID" "$CANDIDATE_MD5" >"$PHASE_TMP") &&
   "$R/fault-evidence" sync && mv -f "$PHASE_TMP" "$GUARD" && "$R/fault-evidence" sync; then
   return 0
  fi
  rm -f "$PHASE_TMP"
 fi
 return 1
''')
    # rc3 boot ff533369: diagnostics named NATIVE_VIDEO, which this build removes (set -u).
    s=replace_once(s,'video=$NATIVE_VIDEO ','video=managed ')
    s=replace_once(s,'daemon-exec native-service-client; do','daemon-exec native-service-client face-status speaker-guardian cammy-ui fault-evidence; do')
    # 0.7 touch menu: cammy-ui runs beside sdvcam, stopped with the other clients.
    s=replace_once(s,' for file in "$R/wpa.pid" "$R/dhcp.pid"; do',' for file in "$R/wpa.pid" "$R/dhcp.pid" "$R/ui.pid"; do')
    s=replace_once(s,'   wpa_supplicant|busybox) kill','   wpa_supplicant|busybox|cammy-ui) kill')
    s=replace_once(s,' rm -f /tmp/cammy-supervisor.pid "$R/wpa.pid" "$R/dhcp.pid" "$R/portal.pid"',' rm -f /tmp/cammy-supervisor.pid "$R/wpa.pid" "$R/dhcp.pid" "$R/portal.pid" "$R/ui.pid"')
    s=replace_once(s,'for data in shared-capture.sh shared-capture.fragment native-media-start.sh; do','for data in shared-capture.sh shared-capture.fragment native-media-start.sh native-release.conf rc-health.sh rc-media-worker.sh speaker.conf; do')
    a=s.index('NATIVE_VIDEO=0\n');b=s.index('SHARED_AUDIO=0\n',a);s=s[:a]+'# Media is prepared only by an explicit authenticated request.\n'+s[b:]
    s=replace_once(s,'start_portal || cleanup','start_portal || { /bin/sh "$R/rc-health.sh" snapshot portal-start; cleanup; }')
    s=replace_once(s,'echo DRIVER_UNAVAILABLE >"$R/state"; exit 1',
                   'echo DRIVER_UNAVAILABLE >"$R/state"; /bin/sh "$R/rc-health.sh" snapshot radio-load; exit 1')
    s=replace_once(s,'--port 8787 --stock-preview "$@"','--port 8787 --stock-preview --native-face --native-input --strict-control --managed-media --speaker-root "$R" "$@"')
    # Loading gate (0.7.12 candidate): cammy-ui starts as soon as the payload is verified,
    # before the stock settle, so a CammyOS pick shows what startup is waiting for. It opens
    # no input device and polls nothing of stock's until the settle phase is over (cammy_ui.c).
    s=replace_once(s,'phase stock_settle\n','''start_ui() {
 "$R/cammy-ui" --run-dir "$R" >>"$R/ui.log" 2>&1 &
 echo "$!" >"$R/ui.pid"
}
RELEASE_ID=$(sed -n 's/^release_id=//p' "$R/native-release.conf")
CANDIDATE_MD5=$(sed -n 's/^md5=//p' /run/cammy-select/launch.stamp)
case "$RELEASE_ID:$CANDIDATE_MD5" in *[!0-9a-f:]*) exit 1;; esac
[ "${#RELEASE_ID}" -eq 24 ] && [ "${#CANDIDATE_MD5}" -eq 32 ] || exit 1
"$R/fault-evidence" trail "$BOOT_ID" "$RELEASE_ID" >/dev/null 2>&1 &
TRAIL_PID=$!
start_ui
UI_STARTS=1
phase stock_settle
''')
    # The settle kept startup off the vendor app's first seconds, but as a fixed 30 s after the
    # chooser's pick (17 s) it held Wi-Fi until 47 s. Wait for 35 s of uptime instead: the same
    # distance from power-on as the reset it guards against, 12 s sooner (2026-11-24, 0.9.11).
    s=replace_once(s,'sleep 30\nphase radio_load\n','''SETTLE_UNTIL=35
while { read -r UP _ </proc/uptime; } 2>/dev/null && [ "${UP%%.*}" -lt "$SETTLE_UNTIL" ]; do sleep 1; done
phase radio_load
''')
    a=s.index('if [ "$NATIVE_VIDEO" = 1 ]; then');b=s.index('MISSES=0\n',a)
    s=s[:a]+'''phase menu
HEALTH_FAULT=0
MEDIA_PID=
MEDIA_TICK=
phase idle_ready || cleanup
HEALTH_PASSES=0
NETWORK_PASSES=0
D=$R/release-health
M=$R/media-prepare
same_process() {
 { IFS= read -r PROC_LINE <"/proc/$1/stat"; } 2>/dev/null || return 1
 case "$PROC_LINE" in *') '*) ;; *) return 1;; esac
 set -- "$2" ${PROC_LINE##*) }
 [ "$#" -ge 21 ] && [ "$2" != Z ] || return 1
 EXPECTED=$1
 shift 20
 [ "$1" = "$EXPECTED" ]
}
steady_health() {
 [ -f "$D/committed" ] && [ ! -e /etc/cammy/native-select.crash-used ] &&
  [ ! -L /etc/cammy/native-select.crash-used ] &&
  [ ! -e /etc/cammy/native-select.radio-used ] && [ ! -L /etc/cammy/native-select.radio-used ] &&
  [ ! -e "$GUARD" ] && [ ! -L "$GUARD" ] &&
  [ ! -e "$M/cancel" ] && [ ! -L "$M/cancel" ] &&
  [ ! -L "$M/request" ] && [ -z "$MEDIA_PID" ] || return 1
 MEDIA_STATE=
 if [ -e "$M/state" ]; then IFS= read -r MEDIA_STATE <"$M/state" || return 1; fi
 case "$MEDIA_STATE" in
  ready) [ -f "$M/request" ] && [ -f "$D/media-ready" ] || return 1;;
  refused|'') [ ! -e "$M/request" ] || return 1;;
  *) return 1;;
 esac
 NP= NT=
 read -r NP NT <"$D/owner" || return 1
 case "$NP:$NT" in *[!0-9:]*|:*|*:) return 1;; esac
 same_process "$NP" "$NT" && [ "/proc/$NP/exe" -ef /run/cammy-select/sdvcam ] || return 1
 read -r PP <"$R/portal.pid" || return 1
 case "$PP" in ''|*[!0-9]*) return 1;; esac
 { read -r PC <"/proc/$PP/comm"; } 2>/dev/null && [ "$PC" = cammy-portal ] || return 1
 # A prior GUI miss needs full sampling until the health script clears it.
 [ ! -e "$D/gui-since" ] || return 1
 read -r NET <"$R/state" && read -r HEALTH_NET <"$D/network" && [ "$NET" = "$HEALTH_NET" ] || return 1
 case "$NET" in
  HOME_STATION) ;;
  AP_CammyOS)
   { read -r AP_NAME <"/proc/${AP_PID:-0}/comm"; } 2>/dev/null && [ "$AP_NAME" = hostapd ] &&
    { read -r DHCP_NAME <"/proc/${AP_DHCP_PID:-0}/comm"; } 2>/dev/null && [ "$DHCP_NAME" = udhcpd ] || return 1;;
  *) return 1;;
 esac
 HEALTH_PASSES=$((HEALTH_PASSES + 1))
 [ "$HEALTH_PASSES" -ge 3 ] || return 0
 HEALTH_PASSES=0
 "$R/face-status" >"$D/current" || return 1
 while IFS= read -r line; do
  [ "$line" != "native_pid=$NP" ] || return 0
 done <"$D/current"
 return 1
}
# One bounded control-socket request (fault-evidence wpa), not wpa_cli.
station_connected() {
 "$R/fault-evidence" wpa "$R/ctrl" wlan0 STATUS >/dev/null 2>&1
}
'''+s[b:]
    a=s.index('HEALTHY=0\n');s=s[:a]+s[a:].replace('HEALTHY=0\n','',1)
    a=s.index(' if [ -e "$GUARD" ]; then',s.index('while true; do'));b=s.index(' [ "$(cat "$R/state")" = HOME_STATION ]',a)
    s=s[:a]+''' { read -r P <"$R/ui.pid"; } 2>/dev/null || P=0
 { read -r UI_NAME <"/proc/$P/comm"; } 2>/dev/null || UI_NAME=
 case "$P" in ''|*[!0-9]*) P=0 ;; esac
 if [ "$UI_STARTS" -lt 5 ] && { [ "$P" -le 1 ] || [ "$UI_NAME" != cammy-ui ]; }; then
  start_ui
  UI_STARTS=$((UI_STARTS + 1))
 fi
 # Media preparation runs beside this loop, bounded by its request deadline,
 # so the menu, health and Wi-Fi watches keep running. One worker at a time;
 # its start time rules out a reused PID, and an exited worker is finished.
 if [ -n "$MEDIA_PID" ]; then
  same_process "$MEDIA_PID" "$MEDIA_TICK" || MEDIA_PID=
 fi
 # After a radio fault the boot is left to its latch (or, once committed,
 # simply runs on): health stops sampling a network that is gone.
 if [ "$HEALTH_FAULT" = 0 ] && [ "${RADIO_DOWN:-0}" = 0 ]; then
  if [ -z "$MEDIA_PID" ] && [ -f "$R/media-prepare/request" ] && [ ! -e "$R/media-prepare/work.lock" ]; then
   /bin/sh "$R/rc-media-worker.sh" >"$R/media-worker.log" 2>&1 &
   MEDIA_PID=$!
   MEDIA_TICK=$(awk '{print $22}' "/proc/$MEDIA_PID/stat" 2>/dev/null)
   [ -n "$MEDIA_TICK" ] || MEDIA_PID=
  fi
  # Health is told which worker runs, so it never compares that worker's
  # records mid-transaction; its latch waits for the worker, within a bound.
  steady_health || CAMMY_MEDIA_WORKER=${MEDIA_PID:+$MEDIA_PID:$MEDIA_TICK} /bin/sh "$R/rc-health.sh" >"$R/health.log" 2>&1 || HEALTH_FAULT=1
 fi
 if [ "$HEALTH_FAULT" = 1 ]; then
  CAMMY_MEDIA_WORKER=${MEDIA_PID:+$MEDIA_PID:$MEDIA_TICK} /bin/sh "$R/rc-health.sh" latch >"$R/health-fault-write.log" 2>&1 && HEALTH_FAULT=2
 fi
'''+s[b:]
    a=s.index(' [ "$(cat "$R/state")" = HOME_STATION ]',s.index('while true; do'))
    b=s.index('  MISSES=0',a)
    s=s[:a]+''' read -r NET <"$R/state" || NET=
 [ "$NET" = HOME_STATION ] || continue
 NETWORK_PASSES=$((NETWORK_PASSES + 1))
 # During known losses sample every pass and keep the 40 s fallback bound.
 [ "$MISSES" -ne 0 ] || [ -n "$MEDIA_PID" ] || [ "$NETWORK_PASSES" -ge 6 ] || continue
 NETWORK_PASSES=0
 if station_connected; then
'''+s[b:]
    s=replace_once(s,' echo AP_CammyOS >"$R/state"',''' AP_PID=$(pidof hostapd)
 AP_DHCP_PID=$(pidof udhcpd)
 case "$AP_PID:$AP_DHCP_PID" in *[!0-9:]*|:*|*:) AP_PID=0; AP_DHCP_PID=0;; esac
 echo AP_CammyOS >"$R/state"''')
    # Native init briefly drops the station and its own guard restores it; AP
    # fallback would kill the clients that guard owns. Defer it while a bounded
    # preparation runs; the misses keep counting.
    s=replace_once(s,'  [ "$MISSES" -lt 20 ] || ap_fallback','  [ "$MISSES" -lt 15 ] || [ -n "$MEDIA_PID" ] || ap_fallback')
    # Shutdown: publish cancellation while Wi-Fi and the portal still run, so a
    # running preparation stops at its next gate and restores the mode under
    # its own lease. Then wait, bounded, for that exact worker (PID and start
    # tick). No signal is sent to it or to anything else; a worker still
    # running at the bound is recorded and fails closed on its own.
    s=replace_once(s,"""cleanup() {
 trap '' TERM INT HUP
 stop_clients
""","""cleanup() {
 trap '' TERM INT HUP
 if [ -n "${MEDIA_PID:-}" ]; then
  M=$R/media-prepare
  if [ -d "$M" ] && [ ! -L "$M" ] && [ ! -e "$M/cancel" ] && [ ! -L "$M/cancel" ]; then
   (set -C; echo cancel >"$M/cancel") 2>/dev/null || true
  fi
  MEDIA_STOP=worker-still-running
  T=0
  while [ "$T" -lt 45 ]; do
   MT=$(awk '$3!="Z"{print $22}' "/proc/$MEDIA_PID/stat" 2>/dev/null)
   [ -n "$MT" ] && [ "$MT" = "${MEDIA_TICK:-}" ] || { MEDIA_STOP=worker-exited; break; }
   sleep 1; T=$((T + 1))
  done
  echo "$MEDIA_STOP" >"$R/media-shutdown"
  MEDIA_PID=
 fi
 stop_clients
 if [ "${CLEAN_SHUTDOWN:-0}" = 1 ] && [ "${MEDIA_STOP:-worker-exited}" = worker-exited ] &&
    [ "${TRAIL_PID:-0}" -gt 1 ] &&
    { read -r TRAIL_NAME <"/proc/$TRAIL_PID/comm"; } 2>/dev/null && [ "$TRAIL_NAME" = fault-evidence ]; then
  : >"$R/trail-shutdown"
  kill -TERM "$TRAIL_PID" 2>/dev/null || true
 fi
""")
    s=replace_once(s,'trap cleanup TERM INT','trap "CLEAN_SHUTDOWN=1; cleanup" TERM INT')
    return s.encode()

def media_script(candidate_hash,candidate_md5):
    s=(ROOT/'scripts/native-media-start.sh').read_text()
    if s.count('owned "$NPID" "$NTICK" /usr/bin/sdvcam')!=2:raise ValueError('Native owner contexts changed')
    s=s.replace('owned "$NPID" "$NTICK" /usr/bin/sdvcam','owned "$NPID" "$NTICK" /run/cammy-select/sdvcam')
    # S01 verifies the RAM candidate's MD5 immediately before exec and stamps
    # it; owned() ties the process to that file. No second 11 MB hash here.
    check='''# Integrated release identity, copied from the SHA-verified payload.
[ "$(sed -n 's/^candidate_sha256=//p' "$R/native-release.conf")" = "'''+candidate_hash+'''" ] || exit 1
[ "$(sed -n 's/^md5=//p' /run/cammy-select/launch.stamp)" = "'''+candidate_md5+'''" ] || exit 1
'''
    return replace_once(s,'B=$R/busybox\n','B=$R/busybox\n'+check).encode()

def build(out):
    subprocess.run(['findmnt','-n',os.environ.get('CAMMY_WORK_MOUNT', '/')],check=True,stdout=subprocess.DEVNULL)
    out=Path(out).absolute()
    if not any(out.is_relative_to(p) for p in [ROOT/'private/rc06',ROOT/'build/rc06']):raise ValueError('Ignored local RC output required')
    if out.exists() or any(p.is_symlink() for p in [out,*out.parents]):raise ValueError('No overwrite/symlink outputs')
    work=ROOT/'build/rc06';work.mkdir(parents=True,exist_ok=True)
    cc=str(ROOT/'build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc')
    subprocess.run([cc,'build/arm/arm_legacy_stat.o','-Os','-static','-march=armv7-a','-mfpu=neon-vfpv4','-mfloat-abi=hard','-std=c11','-Wall','-Wextra','-Werror','-Wshadow','-Wconversion','-DVERSION="'+VERSION+'"','src/cammy_portal.c','-o',str(work/'cammy-portal')],cwd=ROOT,check=True,capture_output=True)
    subprocess.run([cc,'build/arm/arm_legacy_stat.o','-Os','-static','-march=armv7-a','-mfpu=vfpv3-d16',
                    '-mfloat-abi=hard','-std=c11','-Wall','-Wextra','-Werror','src/boot/fault_evidence.c',
                    '-o',str(work/'fault-evidence')],cwd=ROOT,check=True)
    subprocess.run(['make','-s','-C',str(ROOT/'native/speaker_probe'),'arm'],check=True,capture_output=True)
    subprocess.run(['make','-s','-B','-C',str(ROOT/'native/ui'),'arm'],check=True,capture_output=True)
    # The boot client was a stale prebuilt in rc3; always compile it from source.
    subprocess.run([cc,'build/arm/arm_legacy_stat.o','-Os','-static','-march=armv7-a','-mfpu=neon-vfpv4','-mfloat-abi=hard','-std=c11','-Wall','-Wextra','-Werror','-Wformat=2','-Wshadow','-Wconversion','src/boot/native_service_client.c','-o','build/arm/native-service-client'],cwd=ROOT,check=True)
    subprocess.run(['make','-s','-C',str(ROOT/'native/face_service'),'assets','device','status-tool'],check=True,capture_output=True)
    library=(ROOT/'build/face-service/libcammy_face_service.so').read_bytes()
    assets=json.loads((ROOT/'build/face-service/assets.json').read_text())['packs']['cammy']
    if not assets.get('available'):raise ValueError('The Cammy art is missing')
    face_bytes=len(library)+FACE_DECODE_BYTES
    if face_bytes>FACE_LIBRARY_BUDGET:raise ValueError('Face library and decode buffers exceed the camera RAM budget')
    audit=F.library_audit(ROOT/'build/face-service/libcammy_face_service.so',ROOT/'build/native-loader/device-rootfs',F.EXPORTS,profile='face-service')
    if not audit['pass']:raise ValueError('Native service ABI audit failed')
    source=F.SOURCE.read_bytes();candidate,scope=F.patch(source,sha(library))
    from tools.shared_capture_profile import fragment
    inputs={'cammy-ui':ROOT/'build/cammy-ui/cammy-ui-arm','speaker-guardian':ROOT/'build/speaker-probe/speaker-probe-arm','cammy-portal':work/'cammy-portal','busybox':ROOT/'build/arm/busybox-dhcp','wpa_supplicant':ROOT/'build/arm/wpa_supplicant','wpa_cli':ROOT/'build/arm/wpa_cli','native-service-client':ROOT/'build/arm/native-service-client','daemon-exec':ROOT/'build/arm/daemon-exec','face-status':ROOT/'build/face-service/face-status','fault-evidence':work/'fault-evidence','dhcp.sh':ROOT/'scripts/home-dhcp.sh','shared-capture.sh':ROOT/'scripts/shared-capture.sh'}
    payload={name:p.read_bytes() for name,p in inputs.items()}
    # musl's time64 stat/fstat use statx, which the camera's 4.9 kernel answers with EINVAL;
    # cammy-ui shipped without build/arm/arm_legacy_stat.o and its menu never opened (2026-11-16).
    nm=str(ROOT/'build/toolchain/bin/arm-buildroot-linux-musleabihf-nm')
    for name in ('cammy-ui','speaker-guardian','cammy-portal','native-service-client','face-status','fault-evidence'):
        symbols=subprocess.run([nm,str(inputs[name])],check=True,capture_output=True,text=True).stdout.split('\n')
        if any(l.endswith((' T __fstatat',' W __fstat_time64',' W __fstatat_time64')) for l in symbols):
            raise ValueError(name+' links musl stat without build/arm/arm_legacy_stat.o')
    strip=ROOT/'build/toolchain/bin/arm-buildroot-linux-musleabihf-strip'
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        for name,data in payload.items():
            if not data.startswith(b'\x7fELF'):
                continue
            staged=Path(tmp)/name
            subprocess.run([str(strip),'--strip-all','-o',str(staged),str(inputs[name])],check=True)
            payload[name]=staged.read_bytes()
    # Version-slot seed binds built binaries and all shell source templates;
    # complete generated members are separately covered by the outer manifest.
    seed={'version':VERSION,'candidate':sha(candidate),'library':sha(library),'portal':sha(payload['cammy-portal']),'speaker_guardian':sha(payload['speaker-guardian']),'helper':BASE_SHA,'fault_evidence':sha(payload['fault-evidence']),
          'cammy_face':assets,'face_bytes':face_bytes,'sources':source_identities(),'device_binaries':{name:sha(data) for name,data in payload.items() if data.startswith(b'\x7fELF')},'builder':sha(Path(__file__).read_bytes())}
    release_id=sha(json.dumps(seed,sort_keys=True).encode())[:24];slot='CammyOS-releases/'+release_id;card='/mnt/extsd/'+slot+'/runtime'
    hp=helper(slot);base=generate_proposal(SAVED_STOCK_S01APP.read_bytes(),hp)
    s01=replace_once(base.replacement_text,'\nSELECT_GATE=blocked\n','\nSELECT_GATE='+GATE+'\n').encode()
    from tools.speaker_profile import profile as speaker_profile
    payload['speaker.conf']=speaker_profile().encode()
    payload.update({'startup.sh':runtime(slot),'native-media-start.sh':media_script(sha(candidate),md5(candidate)),'RELEASE':(VERSION+'\n').encode(),'shared-capture.fragment':fragment().encode(),'enable-shared-capture':b'experimental-shared-capture-v1\n','boot-helper.sh':hp})
    conf=f'format=cammy-native-release/1\nrelease_id={release_id}\nversion={VERSION}\nclass={CLASS}\ncandidate_sha256={sha(candidate)}\ncandidate_md5={md5(candidate)}\nlibrary_sha256={sha(library)}\n'
    payload['native-release.conf']=conf.encode()
    from tools import rc_runtime_integrity as I
    for name,template in [('rc-health.sh','health.sh'),('rc-media-worker.sh','media-worker.sh')]:payload[name]=I.rc_script(name,(ROOT/'scripts/rc06'/template).read_text(),card)
    # The runtime reads its payload only from the RAM copies S99 staged and checked.
    payload['startup.sh']=I.ram_runtime(payload['startup.sh'],set(payload)|{'SHA256SUMS'})
    payload['SHA256SUMS']=''.join(sha(data)+'  '+name+'\n' for name,data in sorted(payload.items())).encode()
    # S99 pins the manifest digest (S99 is outside it, so no self-reference) and
    # runs only private RAM copies it verified after copying them from the card.
    s99=I.bootstrap_s99((ROOT/'scripts/S99cammy-tf').read_text(),card,payload, require_candidate=True)
    values={'format':R.ALLOW_FORMAT,'s01app_md5':md5(s01),'source_md5':md5(source),'source_size':str(len(source)),
            'candidate':f'{md5(candidate)}:{len(candidate)}:{CLASS}','library':f'{sha(library)}:{md5(library)}:{len(library)}'}
    attempt=sha(candidate+library+s01)[:32]
    members={f'tf/{slot}/runtime/{n}':b for n,b in payload.items()}
    members.update({f'tf/{slot}/native/candidate.bin':candidate,f'tf/{slot}/native/face.so':library,
        f'tf/{slot}/native/arm':R.arm_record(attempt,md5(candidate),len(candidate),CLASS,sha(library),md5(library),len(library)),
        'bootstrap/S01app':s01,'bootstrap/S99cammy-tf':s99.encode(),'bootstrap/native-select.allow':R.render(R.ALLOW_KEYS,values),
        'bootstrap/native-select.enabled':b'mode=disabled\n','templates/native-select.enabled.select':b'mode=select\n',
        'templates/native-select.resident':f'format=cammy-resident/1\nrelease_id={release_id}\n'.encode(),
        # Opt-in for one automatic retry after an interrupted late startup; needs the resident record too.
        'templates/native-select.retry':f'format=cammy-power-retry/1\nrelease_id={release_id}\ncandidate_md5={md5(candidate)}\n'.encode(),
        'rollback/S01app.stock':SAVED_STOCK_S01APP.read_bytes(),'rollback/S99cammy-tf.previous':(ROOT/'scripts/S99cammy-tf').read_bytes()})
    # Include only original host/SDK sources and artwork, never private config.
    for folder in ['host','sdk']:
        for p in sorted((ROOT/folder).rglob('*')):
            if p.is_file() and p.suffix in ('.py','.html','.js','.css','.toml') and '__pycache__' not in p.parts:
                members['desktop/'+str(p.relative_to(ROOT))]=p.read_bytes()
    members['desktop/assets/faces/pikachu.json']=(ROOT/'assets/faces/pikachu.json').read_bytes()
    members['desktop/tools/c3/cammy-calendar-broker.service']=(ROOT/'tools/c3/cammy-calendar-broker.service').read_bytes()
    for path in DESKTOP_MAINTENANCE_INPUTS:
        members['desktop/'+path]=(ROOT/path).read_bytes()
    # Ship the already-built Linux renderer/editor/presets and their license;
    # no source checkout, private config, model cache or venv is copied.
    members['desktop/build/paramface/libcammy_paramface.so']=(ROOT/'build/paramface/libcammy_paramface.so').read_bytes()
    members['desktop/LICENSE-ParamFace']=(ROOT/'downloads/stackchan-face-editor/LICENSE').read_bytes()
    for folder in ['faces','web/dist']:
        for p in sorted((ROOT/'downloads/stackchan-face-editor'/folder).rglob('*')):
            if p.is_symlink():raise ValueError('Unexpected editor symlink')
            if p.is_file():members['desktop/downloads/stackchan-face-editor/'+str(p.relative_to(ROOT/'downloads/stackchan-face-editor'))]=p.read_bytes()
    members['controls/m105.json']=(ROOT/'assets/controls/m105.json').read_bytes()
    for name in ['RELEASE-0.6.0-CANDIDATE.md','M105-CONTROL-INVENTORY.md','RC06-INSTALL-REVIEW.md','SPEAKER-ROUTING-2026-11-15.md','RELEASE-0.6.0-RC2.md']:
        members['docs/'+name]=(ROOT/'docs'/name).read_bytes()
    members['installation-plan.json']=(json.dumps({
        'format':'cammy-bootstrap-review/1','release_id':release_id,'executed':False,
        'kernel_or_full_flash_write':False,'automatic_reboot':False,
        'tf_destination':'/mnt/extsd/'+slot,
        'internal_files':[{'source':'bootstrap/S01app','target':'/etc/init.d/S01app','before_sha256':sha(SAVED_STOCK_S01APP.read_bytes()),'after_sha256':sha(s01),'mode':'0755'},
            {'source':'bootstrap/S99cammy-tf','target':'/etc/init.d/S99cammy-tf','before_sha256':sha((ROOT/'scripts/S99cammy-tf').read_bytes()),'after_sha256':sha(s99.encode()),'mode':'0755'},
            {'source':'bootstrap/native-select.allow','target':'/etc/cammy/native-select.allow','mode':'0600'},
            {'source':'bootstrap/native-select.enabled','target':'/etc/cammy/native-select.enabled','mode':'0600'}],
        'credentials':'reuse existing private home.conf and portal.token; not included',
        'resident_policy':'opt-in template only; do not enable before acceptance',
        'power_retry_policy':'legacy opt-in only; separate from crash recovery',
        'crash_retry_policy':'one boot for attributable native loss or a release-bound late interruption on an established resident release; 900 seconds of sampled health to replenish',
        'tf_fault_evidence':'up to four 32 KiB reports plus one partial, when mounted TF has space',
        'per_boot_tf_writes':True,
        'first_trial':'one manually powered candidate boot; then stock without rearm',
        'rollback':['disable selector','restore exact stock S01app and previous S99','preserve fault/claim evidence','manual power cycle and verify stock'],
        'hardware_acceptance':'three cold boots; one hour combined; Stop/disconnect; stock rollback'
    },sort_keys=True,indent=2)+'\n').encode()
    report={'format':'cammy-integrated-candidate/1','version':VERSION,'release_id':release_id,'slot':slot,'class':CLASS,'attempt':attempt,'build_seed':seed,
        'deployed':False,'activation_eligible':False,'hardware_accepted':False,'default_mode':'disabled','resident_opt_in':False,'power_retry_opt_in':False,'per_boot_tf_writes':True,
        'automatic_media_initialization':False,'native_animation_hardware_verified':False,'speaker_pcm_api':True,'speaker_wifi_hardware_verified':False,'speaker_usb_speech_verified':True,'source_stock_sha256':sha(source),'candidate_sha256':sha(candidate),'library_sha256':sha(library),'library_abi':audit,'candidate_scope':scope,
        'known_blockers':['startup reset cause unresolved','new service/input/boot policy require integrated hardware acceptance','three cold boots and one-hour combined run not performed','new Wi-Fi speaker worker and concurrent A/V echo behavior need hardware acceptance',
            'power-loss retry and internal-only rearm verified only offline; no real power cut, reboot or physical power-key timing tested',
            'installing changes /etc/init.d/S01app (selector retry); the allowlist s01app_md5 changes with it'],
        'files':{n:{'sha256':sha(b),'bytes':len(b)} for n,b in sorted(members.items())}}
    rc_wifi_policy.validate_members(members, slot)
    for name, data in members.items():
        if name.startswith('tf/'):
            rc_wifi_policy.validate_tf_content(data)
    out.mkdir(parents=True,mode=0o700)
    for name,data in members.items():
        p=out/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);p.chmod(0o600)
    (out/'candidate.json').write_text(json.dumps(report,indent=2)+'\n');(out/'candidate.json').chmod(0o600)
    return report
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);args=p.parse_args();result=build(args.out)
    print(json.dumps({k:result[k] for k in ['version','release_id','deployed','activation_eligible','known_blockers']},indent=2))
