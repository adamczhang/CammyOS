"""One operator-observed CammyOS stock-button trial through existing USB root.

Finite left/right or media button input, with explicit native UI context. No
automatic retry, native patch, driver or executable deployment.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'host'), str(ROOT/'sdk')]
from cammy import Cammy
from cammy_buttons import tap_once
from tools import bridge_smoke as smoke
from tools.vision_trial import preserved


def preserve_button_identity(before, after):
    # Snapshot separately checks fd3 still targets /dev/watchdog. Keep its
    # actual fdinfo plus process/boot/files/maps checks. /proc symlink inode and
    # timestamp are ephemeral metadata, not watchdog ownership or configuration.
    normalized = []
    for snapshot in (before, after):
        fdinfo, separator, node = snapshot['wd_info'].partition('WDNODE=')
        if not separator or not node.rstrip().endswith('-> /dev/watchdog'):
            raise ValueError('Watchdog link target evidence missing')
        normalized.append({**snapshot, 'wd_info': fdinfo.strip()})
    preserved(*normalized)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tap', choices=('left', 'right', 'center', 'playback_cycle'), required=True)
    context = parser.add_mutually_exclusive_group(required=True)
    context.add_argument('--live-view-confirmed', action='store_true')
    context.add_argument('--viewer-confirmed', action='store_true')
    parser.add_argument('--adb-port', type=int, choices=(5037, 5038), required=True)
    parser.add_argument('--hold-ms', type=int, choices=(0, 150, 5000), default=0)
    args = parser.parse_args()
    if args.tap in ('left','right') and not args.live_view_confirmed:
        parser.error('Gimbal trials require normal live view')
    if args.tap == 'center' and not args.viewer_confirmed:
        parser.error('Center play/pause trial requires the viewer')
    smoke.mounted()
    report = {'temperatures_c': smoke.temperatures(), 'operator_observation_pending': True}
    report['ui_context_confirmed'] = 'live' if args.live_view_confirmed else 'viewer'
    gateway = smoke.gateway()
    if not gateway.get('connected') or gateway['/v1/state']['owner'] is not None:
        raise RuntimeError('Connected idle CammyOS body required; no owner takeover')
    config = json.loads((ROOT/'private/host/config.json').read_text())
    token = Path(config['token_file']).read_text().strip()
    os.environ['LD_LIBRARY_PATH'] = ':'.join(str(smoke.DEPS/x) for x in
        ['lib/x86_64-linux-gnu/android', 'lib/x86_64-linux-gnu'])
    reader = smoke.AdbReader(smoke.DEPS/'lib/android-sdk/platform-tools/adb', '20080411', args.adb_port)
    before = smoke.snapshot(reader)
    prefix = str(time.time_ns())+'-'+args.tap
    smoke.save(ROOT/'private/stock-controls'/(prefix+'-before.json'), before)
    client = Cammy('http://127.0.0.1:8790', token, agent='stock-button-trial', timeout=3)
    acquired = False
    try:
        client.acquire(ttl=45); acquired = True
        report.update(tap_once(reader, client, args.tap, before, args.hold_ms))
        after = smoke.snapshot(reader)
        smoke.save(ROOT/'private/stock-controls'/(prefix+'-after.json'), after)
        preserve_button_identity(before, after)
        report['stock_identity_preserved'] = True
        report['boot'] = before['boot']
    finally:
        if acquired:
            try:
                report['release'] = client.release()
            except Exception as exc:
                report['release_error'] = type(exc).__name__
        suffix = '-'+str(args.hold_ms)+'ms' if args.hold_ms else ''
        path = ROOT/'private/stock-controls'/('remote-'+args.tap+suffix+'.json')
        smoke.save(path, report)
        smoke.save(path.with_name(str(time.time_ns())+'-'+path.name), report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
