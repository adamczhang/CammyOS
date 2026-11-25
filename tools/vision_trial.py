"""One finite native-photo trial through an ephemeral authenticated PC gateway.

Default: read-only readiness. --capture-one: temporarily select photo mode, save
ONE photo on TF, retrieve via SDK and restore mode. No code/firmware deployment.
Run the capture only within the operator-approved hardware test scope.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from c3 import discover  # noqa: E402
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
sys.path.insert(0, str(ROOT / 'sdk'))
from cammy_host import Gateway, make_server
from cammy_vision import NativePhoto, jpeg_info
from cammy import Cammy
from tools import bridge_smoke as smoke


def executable_files(snapshot):
    """Ignore allocator/thread annotations, retain executable file mappings."""
    rows = []
    for line in snapshot['native_maps'].splitlines():
        fields = line.split()
        if len(fields) < 6:
            continue
        tail = fields[5:]
        # This vendor kernel inserts allocation/thread accounting before pathname.
        if tail[0].startswith('a:'):
            tail = tail[2:]
        path = ' '.join(tail)
        # Vendor DMA buffers are mapped executable despite being image data.
        if ('x' in fields[1] and path.startswith('/') and path != '/dmabuf (deleted)'
                and not path.startswith(('/dev/', '/SYSV'))):
            rows.append(fields[:5] + [path])
    return rows


def preserved(before, after):
    for key in ('boot', 'native', 'hashes', 'kernel', 'cmdline', 'wd_info', 'mounts', 'selection'):
        if before[key] != after[key]:
            raise ValueError('Unexpected stock state change: ' + key)
    if not executable_files(before) or executable_files(before) != executable_files(after):
        raise ValueError('Unexpected native executable file mapping change')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-one', action='store_true')
    parser.add_argument('--count', type=int, choices=(1, 2, 3), default=1,
                        help='Bounded snapshot sequence; requires --capture-one')
    parser.add_argument('--resident-gateway', action='store_true',
                        help='Use the configured running desktop service for capture')
    parser.add_argument('--adb-port', type=int, choices=(5037, 5038), default=5038,
                        help='Existing PC ADB server for read-only preservation checks')
    args = parser.parse_args()
    if args.count != 1 and not args.capture_one:
        parser.error('--count requires --capture-one')
    smoke.mounted()
    temperatures = smoke.temperatures()
    config = json.loads((ROOT / 'private/host/config.json').read_text())
    token = Path(config['token_file']).read_text().strip()
    os.environ['LD_LIBRARY_PATH'] = ':'.join(str(smoke.DEPS / p) for p in
        ('lib/x86_64-linux-gnu/android', 'lib/x86_64-linux-gnu'))
    reader = smoke.AdbReader(smoke.DEPS / 'lib/android-sdk/platform-tools/adb', '20080411', args.adb_port)
    before = smoke.snapshot(reader)
    smoke.validate_gateway(smoke.gateway())
    endpoint = discover.portal()
    native = NativePhoto(endpoint)
    original, files = native.status(), native.files()
    if original != 0:
        raise ValueError('This reviewed trial requires idle normal video mode 0')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = ROOT / 'private/vision-milestone' / (stamp + ('-capture' if args.capture_one else '-preflight'))
    out.mkdir(mode=0o700)
    smoke.save(out / 'before.json', before)
    report = {'camera_actions_authorized_by_this_command': args.capture_one,
        'mode_before': original, 'file_count_before': len(files),
        'boot_id': before['boot'], 'temperatures_c': temperatures,
        'capture_attempted': False, 'firmware_deployed': False,
        'continuous_video': False, 'hardware_cleanup_confirmed': False}
    gateway = server = thread = client = None
    try:
        probe_client = Cammy('http://127.0.0.1:' + str(config['port']), token, agent='vision-preflight')
        probe = probe_client.probe_start('runtime')
        deadline = time.monotonic() + 5
        while probe['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.1)
            probe = probe_client.probe_status(probe['job_id'])
        sample = probe.get('result', {}).get('boot_id', {})
        if probe['status'] != 'completed' or sample.get('available') is not True or sample.get('truncated') is not False or sample.get('value', '').strip() != before['boot']:
            raise RuntimeError('Runtime probe boot provenance did not match USB reference')
        report['runtime_probe_boot_matches'] = True
        if args.capture_one:
            if args.resident_gateway:
                port = config['port']
                report['gateway'] = 'resident'
            else:
                gateway = Gateway(token, [endpoint], native_photo=True)
                if not gateway.refresh()['connected']:
                    raise RuntimeError('Ephemeral gateway cannot reach real camera')
                server = make_server(gateway, ('127.0.0.1', 0))
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                port = server.server_port
                report['gateway'] = 'ephemeral'
            client = Cammy('http://127.0.0.1:' + str(port), token, agent='vision-trial')
            client.acquire(ttl=60)
            report['capture_attempted'] = True
            report['frames'] = []
            for index in range(args.count):
                started = time.monotonic()
                result = client.capture_frame()
                metadata = result['metadata']
                if metadata.get('synthetic') is not False or metadata.get('boot_id') != before['boot']:
                    raise RuntimeError('Unexpected frame provenance')
                report.update(frame=metadata, decoded=jpeg_info(result['jpeg']))
                image = out / ('frame.jpg' if args.count == 1 else 'frame-' + str(index + 1) + '.jpg')
                image.write_bytes(result['jpeg'])
                image.chmod(0o600)
                current_files = native.files()
                if native.status() != original or current_files - files != {metadata['filename']}:
                    raise RuntimeError('Mode restoration or exactly-one-new-photo check failed')
                files = current_files
                report['one_photo_and_mode_restored'] = True
                report['image_path'] = str(image)
                report['frames'].append({'metadata': metadata, 'image_path': str(image),
                                         'transaction_seconds': round(time.monotonic() - started, 3)})
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        if gateway:
            gateway.vision.close()
            if gateway.vision.job:
                report['final_job'] = gateway.vision.get('vision-trial', gateway.vision.job['id'])
        if client:
            try:
                report['release'] = client.release()
            except Exception as exc:
                report['release_error'] = str(exc)
        if server:
            server.shutdown()
            server.server_close()
            thread.join(5)
        try:
            report['mode_after'] = native.status()
            after = smoke.snapshot(reader)
            smoke.save(out / 'after.json', after)
            preserved(before, after)
            smoke.validate_gateway(smoke.gateway())
            report['stock_state_preserved'] = True
        except Exception as exc:
            report['preservation_error'] = str(exc)
        report['pass'] = not any(k.endswith('error') for k in report) and report.get('stock_state_preserved') is True
        smoke.save(out / 'result.json', report)
        smoke.save(ROOT / 'private/vision-milestone/current-trial.json', report)
    print(json.dumps({'pass': report['pass'], 'capture_attempted': report['capture_attempted'],
        'report': str(out / 'result.json'), 'image': report.get('image_path'),
        'error': report.get('error'), 'preservation_error': report.get('preservation_error')}))
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
