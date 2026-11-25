"""One operator-run, three-second stock video recording; not a live-video API.

Known phone-app 1100 start/stop controls only. Creates one TF movie, downloads
it privately and probes/decodes it locally. No firmware, mode, radio or app edit.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from c3 import discover  # noqa: E402
import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit, unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'host'), str(ROOT / 'sdk')]
from cammy_vision import NativePhoto, DIRECTORY
from cammy import Cammy
from tools import bridge_smoke as smoke
from tools.vision_trial import preserved


class MovieListing(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names = set()

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        link = urlsplit(dict(attrs).get('href', ''))
        if link.scheme or link.netloc or link.query or link.fragment:
            return
        path = unquote(link.path)
        name = path[len(DIRECTORY):] if path.startswith(DIRECTORY) else path
        if '..' not in name and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.(?:mp4|mov)', name, re.I):
            self.names.add(name)


class Movie(NativePhoto):
    SETTING_INDEX = {'ml_record_resolution': 0, 'ml_video_quality': 7}

    def settings(self):
        result = json.loads(self._read('/api/getdeviceinfo/?custom=1&cmd=2002'))
        values = {item['index']: item['value'] for item in result['info']}
        return {key: values[index] for key, index in self.SETTING_INDEX.items()}

    def setting(self, key, value):
        allowed = {'ml_record_resolution': range(6), 'ml_video_quality': range(3)}
        if key not in allowed or type(value) is not int or value not in allowed[key]:
            raise ValueError('Only evidenced resolution/quality presets are allowed')
        if self.recording() != 0:
            raise RuntimeError('Do not change settings during recording')
        # Phone-app generic menu route. Readback, rather than reply parsing,
        # establishes that the menu value was actually changed.
        self._read('/api/setdeviceinfo/?custom=1&mode=0&item=' + key + '&par=' + str(value), 'POST')
        for _ in range(30):
            if self.settings()[key] == value:
                return
            time.sleep(.1)
        raise RuntimeError('Native setting did not settle: ' + key)

    def recording(self):
        mode = json.loads(self._read('/api/getdeviceinfo/?custom=1&cmd=2008'))
        state = json.loads(self._read('/api/getdeviceinfo/?custom=1&cmd=2005'))
        if mode != {'current_mode': 0, 'curworkmodename': 'NormalRecordeMode'}:
            raise RuntimeError('Expected normal video mode; will not switch modes')
        value = state.get('RecodStatus')
        if type(value) is not int or value not in (0, 1):
            raise RuntimeError('Unknown native recording state')
        return value

    def movies(self):
        parser = MovieListing()
        parser.feed(self._read(DIRECTORY, limit=512 * 1024).decode())
        return parser.names


def record_three_seconds(native, client, sleep=time.sleep):
    """Finite experiment: never retry start, and always attempt its own stop."""
    state = client.state()
    if state['owner'] != client.agent or state['lease_remaining_seconds'] < 25 or state['preview_active']:
        raise RuntimeError('Matching idle lease with at least25seconds required')
    if native.recording() != 0:
        raise RuntimeError('Will not interfere with an existing recording')
    start_maybe_sent = False
    try:
        start_maybe_sent = True
        native._post(1100, 1)
        for _ in range(20):
            if native.recording() == 1:
                break
            sleep(.15)
        else:
            raise RuntimeError('Recording start did not settle; stopping once')
        sleep(3)
    finally:
        if start_maybe_sent:
            native._post(1100, 0)
            for _ in range(24):
                sleep(.15)
                if native.recording() == 0:
                    break
            else:
                raise RuntimeError('Native stop not confirmed; inspect camera, do not restart trial')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record-three-seconds', action='store_true', required=True)
    parser.add_argument('--720p-low', dest='low', action='store_true',
                        help='Temporarily test evidenced 720P/30FPS and Low quality, restoring both afterwards')
    parser.add_argument('--acoustic-cue', action='store_true',
                        help='Play one bounded P15 tone cue, collect separately labelled P15 and camera audio')
    parser.add_argument('--adb-port', type=int, choices=(5037, 5038), default=5038,
                        help='Existing PC ADB server to use for read-only preservation checks')
    args = parser.parse_args()
    smoke.mounted()
    report = {'continuous_video': False, 'hardware_cleanup_confirmed': False,
              'firmware_deployed': False, 'temperatures_c': smoke.temperatures()}
    smoke.validate_gateway(smoke.gateway())
    config = json.loads((ROOT / 'private/host/config.json').read_text())
    token = Path(config['token_file']).read_text().strip()
    os.environ['LD_LIBRARY_PATH'] = ':'.join(str(smoke.DEPS / p) for p in
        ('lib/x86_64-linux-gnu/android', 'lib/x86_64-linux-gnu'))
    reader = smoke.AdbReader(smoke.DEPS / 'lib/android-sdk/platform-tools/adb', '20080411', args.adb_port)
    before = smoke.snapshot(reader)
    native = Movie(discover.portal())
    if native.recording() != 0:
        raise RuntimeError('Expected idle video mode')
    baseline = native.movies()
    original_settings = native.settings()
    report['settings_before'] = original_settings
    settings_maybe_changed = False
    client = Cammy('http://127.0.0.1:' + str(config['port']), token, agent='clip-trial')
    out = ROOT / 'private/vision-milestone' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-clip')
    out.mkdir(mode=0o700)
    smoke.save(out / 'before.json', before)
    cue = None
    try:
        client.acquire(ttl=60)
        if args.low:
            settings_maybe_changed = True
            native.setting('ml_record_resolution', 5)
            native.setting('ml_video_quality', 2)
            report['settings_during'] = native.settings()
        report['start_requested_at'] = datetime.now(timezone.utc).isoformat()
        if args.acoustic_cue:
            from tools.acoustic_evidence import AcousticCue
            cue = AcousticCue(out, client, reader)
            cue.start_reference()
        record_three_seconds(native, client, sleep=cue.sleep if cue else time.sleep)
        report['native_stop_confirmed'] = True
        for _ in range(30):
            files = native.movies() - baseline
            if len(files) > 1:
                raise RuntimeError('Multiple new clips; refusing ambiguous evidence')
            if files:
                break
            time.sleep(.25)
        else:
            raise RuntimeError('No new MP4/MOV after confirmed stop')
        name = next(iter(files))
        raw = native._read(DIRECTORY + name, limit=64 * 1024 * 1024)
        clip = out / ('clip' + Path(name).suffix.lower())
        clip.write_bytes(raw)
        clip.chmod(0o600)
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(clip)],
                               capture_output=True, text=True, timeout=30, check=True)
        report.update(filename=name, bytes=len(raw), clip=str(clip), probe=json.loads(probe.stdout))
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(clip), '-map', '0:v:0', '-map', '0:a?',
                        '-f', 'null', '-'], capture_output=True, timeout=30, check=True)
        report['all_frames_decoded'] = True
        if cue:
            report['acoustic_evidence'] = cue.analyze(clip)
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        if cue:
            cue.close()
            report['acoustic_evidence'] = cue.report
        if settings_maybe_changed:
            try:
                if native.recording() != 0:
                    raise RuntimeError('Recording uncertain; no setting restoration attempted')
                for key, value in original_settings.items():
                    native.setting(key, value)
                report['settings_restored'] = native.settings() == original_settings
                if not report['settings_restored']:
                    raise RuntimeError('Native settings restoration did not match')
            except Exception as exc:
                report['settings_restore_error'] = str(exc)
        if client._lease_active:
            try:
                report['release'] = client.release()
            except Exception as exc:
                report['release_error'] = str(exc)
        try:
            report['recording_after'] = native.recording()
            after = smoke.snapshot(reader)
            smoke.save(out / 'after.json', after)
            preserved(before, after)
            smoke.validate_gateway(smoke.gateway())
            report['stock_state_preserved'] = True
        except Exception as exc:
            report['preservation_error'] = str(exc)
        report['pass'] = report.get('all_frames_decoded') is True and report.get('recording_after') == 0 and not any(k.endswith('error') for k in report)
        if args.acoustic_cue:
            report['pass'] = report['pass'] and report.get('acoustic_evidence', {}).get('camera_heard_p15_cue') is True
        smoke.save(out / 'result.json', report)
        smoke.save(ROOT / 'private/vision-milestone/current-clip.json', report)
    print(json.dumps({'pass': report['pass'], 'report': str(out / 'result.json'), 'clip': report.get('clip'), 'error': report.get('error')}))
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
