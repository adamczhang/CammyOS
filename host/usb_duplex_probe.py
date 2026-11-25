"""Five-second, leased M105 USB audio test through ordinary ALSA drivers.

Default metadata only. Explicit --run plays one quiet cue on the exact M105
speaker, then one P15 cue, while capturing M105 USB and a separate P15 reference.
No mixer edits, raw USB, driver detachment, firmware writes or default devices.
"""
import argparse
import array
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'sdk'), str(ROOT / 'host'), str(ROOT)]
from cammy import Cammy, CammyError
from usb_media_probe import identify, stop_process, audio_summary
from tools.acoustic_evidence import AcousticCue, RATE, analyze_samples, verify_routes


def validate_formats(stream):
    for direction, other in [('Capture:', 'Playback:'), ('Playback:', 'Capture:')]:
        if direction not in stream:
            raise RuntimeError('Both USB audio directions are required')
        section = stream.split(direction, 1)[1].split(other, 1)[0]
        if not all(text in section for text in ('Format: S16_LE', 'Channels: 1', 'Rates: 16000')):
            raise RuntimeError('Exact mono16kHz S16_LE USB formats required')


def supervise_pair(capture, playback, client, validate, start_near, deadline,
                   clock=time.monotonic, sleep=time.sleep, observe=None):
    started, near = clock(), None
    while capture.poll() is None:
        if clock() >= deadline:
            raise RuntimeError('USB duplex deadline exceeded')
        state = client.state()
        if state['owner'] != client.agent or state['lease_remaining_seconds'] <= 0:
            raise RuntimeError('USB duplex lease lost or stopped')
        validate()
        if observe:
            observe(clock() - started)
        if playback.poll() not in (None, 0):
            raise RuntimeError('M105 USB playback failed; no fallback device')
        if near is None and clock() - started >= 2.5:
            if playback.poll() is None:
                raise RuntimeError('Far-end playback did not finish in its phase')
            near = start_near()
        sleep(.05)
    if capture.returncode != 0 or playback.poll() != 0 or near is None:
        raise RuntimeError('Incomplete standard-driver duplex test')
    return near


def run(client, out, sysroot=Path('/sys'), procroot=Path('/proc')):
    device = identify(sysroot)
    card = device['cards'][0]
    validate_formats((procroot / f'asound/card{card}/stream0').read_text())
    identity = client.identity()
    if identity.get('simulation') or identity.get('os') != 'CammyOS':
        raise RuntimeError('Real authenticated CammyOS body required')
    arecord, aplay = shutil.which('arecord'), shutil.which('aplay')
    if not arecord or not aplay:
        raise RuntimeError('Local arecord and aplay are required')
    processes, acquired, reference = [], False, None
    report = {'device': device, 'simulation': False, 'source': 'M105 PCCAM USB standard ALSA',
              'echo_cancellation_verified': False, 'hardware_cleanup_confirmed': False}
    try:
        client.acquire(ttl=15)
        acquired = True
        reference = AcousticCue(out, client)
        reference.start_reference()
        with (out / 'm105-usb.wav').open('xb') as output:
            capture = subprocess.Popen([arecord, '-q', '-D', f'hw:{card},0', '-f', 'S16_LE',
                '-r', '16000', '-c', '1', '-t', 'wav', '-d', '5'], stdout=output, stderr=subprocess.PIPE)
            processes.append(capture)
            playback = subprocess.Popen([aplay, '-q', '-D', f'hw:{card},0', str(reference.cue)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            processes.append(playback)
            def validate():
                if identify(sysroot) != device:
                    raise RuntimeError('Exact M105 USB device changed or disconnected')
            def near():
                reference.player = subprocess.Popen(['pw-play', '--target',
                    reference.report['routes']['Audio/Sink']['name'], '--volume', '.85', str(reference.cue)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                time.sleep(.12)
                graph = json.loads(subprocess.check_output(['pw-dump'], text=True, timeout=2))
                reference.report.update(verify_routes(graph, (reference.player.pid, reference.recorder.pid),
                                                       reference.report['routes']))
                return reference.player
            probe = None
            def observe(elapsed):
                nonlocal probe
                if elapsed < 1 or probe and probe['status'] != 'running':
                    return
                if probe is None:
                    report['host_usb_streams_during_test'] = (procroot / f'asound/card{card}/stream0').read_text()
                    probe = client.probe_start('audio-state')
                else:
                    probe = client.probe_status(probe['job_id'])
                report['camera_audio_state_during_usb'] = probe
            near_process = supervise_pair(capture, playback, client, validate, near, time.monotonic() + 6,
                                          observe=observe)
            near_process.wait(timeout=1)
        reference.close()
        report['usb_audio'] = audio_summary(out / 'm105-usb.wav')
        if report['usb_audio']['frames'] != 5 * RATE:
            raise RuntimeError('M105 USB capture was not exactly five seconds')
        import wave
        with wave.open(str(out / 'm105-usb.wav')) as audio:
            samples = array.array('h', audio.readframes(audio.getnframes()))
        report['usb_far_phase'] = analyze_samples(samples[:int(2.5 * RATE)])
        report['usb_near_phase'] = analyze_samples(samples[int(2.5 * RATE):])
        normalized = out / 'p15-mono16k.wav'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(out / 'p15-reference.wav'),
                        '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(normalized)],
                       capture_output=True, timeout=10, check=True)
        with wave.open(str(normalized)) as audio:
            external = array.array('h', audio.readframes(audio.getnframes()))
        report['external_far_phase'] = analyze_samples(external[:int(2.7 * RATE)])
        report['reference'] = reference.report
        report['camera_speaker_signal_observed'] = (report['external_far_phase']['detected_ordered_cue']
            and reference.report['p15_reference_route_verified'])
        report['camera_usb_mic_signal_observed'] = (report['usb_near_phase']['detected_ordered_cue']
            and reference.report['p15_playback_route_verified'])
        report['both_driver_streams_completed'] = True
        return report
    finally:
        for process in processes:
            stop_process(process)
            if process.stderr:
                process.stderr.close()
        if reference:
            reference.close()
        if acquired:
            try:
                client.release()
            except CammyError as exc:
                if exc.status != 409:
                    raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if not args.run:
        print(json.dumps(identify()))
        return
    from tools import bridge_smoke as smoke
    smoke.mounted()
    smoke.temperatures()
    config = json.loads((ROOT / 'private/host/config.json').read_text())
    client = Cammy('http://127.0.0.1:' + str(config['port']), Path(config['token_file']).read_text().strip(),
                   agent='usb-duplex', timeout=1)
    out = ROOT / 'private/audio-milestone' / (time.strftime('%Y%m%dT%H%M%S') + '-usb')
    out.mkdir(mode=0o700)
    try:
        result = run(client, out)
        result['final_state'] = client.state()
        smoke.save(out / 'result.json', result)
        print(json.dumps({'report': str(out / 'result.json'), 'streams_completed': result['both_driver_streams_completed'],
                          'speaker_observed': result['camera_speaker_signal_observed'],
                          'mic_observed': result['camera_usb_mic_signal_observed']}))
    except Exception as exc:
        smoke.save(out / 'result.json', {'error': type(exc).__name__ + ': ' + str(exc), 'hardware_cleanup_confirmed': False})
        raise


if __name__ == '__main__':
    main()
