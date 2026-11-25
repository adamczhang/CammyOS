"""Known P15 acoustic cue and separately labelled camera/reference evidence."""
import array
import json
import math
from pathlib import Path
import signal
import subprocess
import time
import wave

RATE = 16000
FREQUENCIES = (700, 1100, 1700)


def cue_samples():
    result = array.array('h', [0] * int(.55 * RATE))
    for frequency in FREQUENCIES:
        count = int(.24 * RATE)
        for index in range(count):
            fade = min(1, index / 160, (count - index - 1) / 160)
            result.append(round(11000 * fade * math.sin(2 * math.pi * frequency * index / RATE)))
        result.extend([0] * int(.16 * RATE))
    result.extend([0] * int(.45 * RATE))
    return result


def write_cue(path):
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        out.writeframes(cue_samples().tobytes())


def analyze_samples(samples):
    if len(samples) < RATE:
        raise ValueError('At least one second of mono16kHz samples required')
    size, hop = 1280, 320
    strongest = [{'frequency_hz': f, 'fraction': 0, 'time_seconds': 0, 'rms': 0} for f in FREQUENCIES]
    baseline = samples[:RATE // 4]
    baseline_mean = sum(baseline) / len(baseline)
    baseline_rms = math.sqrt(sum((x - baseline_mean) ** 2 for x in baseline) / len(baseline))
    for start in range(0, len(samples) - size + 1, hop):
        block = samples[start:start + size]
        mean = sum(block) / size
        energy = sum((x - mean) ** 2 for x in block)
        if energy == 0:
            continue
        rms = math.sqrt(energy / size)
        for record in strongest:
            coefficient = 2 * math.cos(2 * math.pi * record['frequency_hz'] / RATE)
            previous = previous2 = 0
            for value in block:
                current = value - mean + coefficient * previous - previous2
                previous2, previous = previous, current
            power = previous * previous + previous2 * previous2 - coefficient * previous * previous2
            fraction = max(0, min(1, 2 * power / (size * energy)))
            if fraction > record['fraction']:
                record.update(fraction=round(fraction, 5), time_seconds=round((start + size / 2) / RATE, 3), rms=round(rms, 3))
    times = [r['time_seconds'] for r in strongest]
    detected = (all(r['fraction'] >= .2 and r['rms'] >= max(30, baseline_rms * 2) for r in strongest)
                and all(.15 <= times[i + 1] - times[i] <= .8 for i in range(2)))
    rms = math.sqrt(sum(x * x for x in samples) / len(samples))
    return {'detected_ordered_cue': detected, 'duration_seconds': len(samples) / RATE,
            'baseline_rms': round(baseline_rms, 3), 'rms': round(rms, 3),
            'peak': max(abs(x) for x in samples), 'tone_peaks': strongest,
            'method': '80ms Goertzel windows,20ms hop; three ordered tones, fraction>=0.2 and RMS>=2x baseline'}


def analyze_wav(path):
    with wave.open(str(path)) as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, RATE):
            raise ValueError('Analysis requires mono16bit16kHz PCM WAV')
        samples = array.array('h', source.readframes(source.getnframes()))
    return analyze_samples(samples)


def verify_routes(graph, pids, routes):
    pids = {str(pid) for pid in pids}
    clients = {str(n['id']) for n in graph if n.get('type', '').endswith(':Client')
               and str(n.get('info', {}).get('props', {}).get('application.process.id')) in pids}
    nodes = {str(n['id']) for n in graph if n.get('type', '').endswith(':Node')
             and str(n.get('info', {}).get('props', {}).get('client.id')) in clients}
    links = [n['info']['props'] for n in graph if n.get('type', '').endswith(':Link')
             and (str(n['info']['props'].get('link.output.node')) in nodes
                  or str(n['info']['props'].get('link.input.node')) in nodes)]
    return {'owned_links': links, 'p15_playback_route_verified': any(
        str(link.get('link.output.node')) in nodes and
        str(link.get('link.input.node')) == str(routes['Audio/Sink']['id']) for link in links),
        'p15_reference_route_verified': any(str(link.get('link.input.node')) in nodes and
        str(link.get('link.output.node')) == str(routes['Audio/Source']['id']) for link in links)}


class AcousticCue:
    def __init__(self, out, client, metadata_reader=None):
        self.out, self.client = Path(out), client
        self.metadata_reader = metadata_reader
        self.player = self.recorder = None
        self.closed = False
        self.nodes = json.loads(subprocess.check_output(['pw-dump'], text=True, timeout=3))
        selected = {}
        for node in self.nodes:
            props = node.get('info', {}).get('props', {})
            if 'Poly_Studio_P15' in props.get('node.name', ''):
                kind = props.get('media.class')
                if kind in ('Audio/Sink', 'Audio/Source'):
                    if kind in selected:
                        raise RuntimeError('Multiple P15 routes: inspect before playback')
                    selected[kind] = {'id': node['id'], 'name': props['node.name']}
        if set(selected) != {'Audio/Sink', 'Audio/Source'}:
            raise RuntimeError('P15 physical sink/source not available')
        self.report = {'source': 'P15 speakers; not camera speaker', 'routes': selected,
                       'stream_volume': .85, 'camera_speaker_tested': False}
        self.cue = self.out / 'p15-cue.wav'
        write_cue(self.cue)

    def start_reference(self):
        self.recorder = subprocess.Popen(['pw-record', '--target', self.report['routes']['Audio/Source']['name'],
            '--rate', '48000', '--channels', '2', '--format', 's16', '--sample-count', '240000',
            str(self.out / 'p15-reference.wav')], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        time.sleep(.2)
        if self.recorder.poll() is not None:
            raise RuntimeError('P15 reference recorder exited before cue')

    def sleep(self, seconds):
        if seconds != 3:
            time.sleep(seconds)
            return
        deadline = time.monotonic() + seconds
        self.player = subprocess.Popen(['pw-play', '--target', self.report['routes']['Audio/Sink']['name'],
            '--volume', '.85', str(self.cue)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.report['playback_started_monotonic'] = time.monotonic()
        time.sleep(.12)
        graph = json.loads(subprocess.check_output(['pw-dump'], text=True, timeout=2))
        self.report.update(verify_routes(graph, (self.player.pid, self.recorder.pid), self.report['routes']))
        self.playback_metadata('before')
        try:
            job = self.client.probe_start('audio-state')
            until = min(deadline, time.monotonic() + 1)
            while job['status'] == 'running' and time.monotonic() < until:
                time.sleep(.05)
                job = self.client.probe_status(job['job_id'])
            self.report['camera_audio_state_during_recording'] = job
        finally:
            time.sleep(max(0, deadline - time.monotonic()))
            self.playback_metadata('after')
            self.close()

    def playback_metadata(self, label):
        if self.metadata_reader:
            try:
                raw = self.metadata_reader.shell("printf 'PLAYBACK\\n'; cat /proc/asound/card0/pcm0p/sub0/status; printf 'CAPTURE\\n'; cat /proc/asound/card0/pcm0c/sub0/status")
                self.report['camera_pcm_status_' + label] = raw.decode(errors='replace')[:2048]
            except Exception as exc:
                self.report['camera_pcm_status_' + label + '_error'] = str(exc)

    def close(self):
        if self.closed:
            return
        self.closed = True
        for name, process in [('player', self.player), ('recorder', self.recorder)]:
            if process is None:
                continue
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
            try:
                _, error = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                _, error = process.communicate(timeout=3)
            self.report[name + '_exit'] = process.returncode
            if error:
                self.report[name + '_stderr'] = error.decode(errors='replace')[:500]

    def analyze(self, clip):
        for label, source in [('camera', clip), ('p15', self.out / 'p15-reference.wav')]:
            output = self.out / (label + '-mono16k.wav')
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(source), '-map', '0:a:0',
                            '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(output)],
                           capture_output=True, timeout=20, check=True)
            self.report[label + '_analysis'] = analyze_wav(output)
        self.report['camera_heard_p15_cue'] = (self.report['camera_analysis']['detected_ordered_cue']
                                              and self.report['p15_playback_route_verified'])
        return self.report
