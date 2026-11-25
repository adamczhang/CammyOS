#!/usr/bin/env python3
"""Acoustic self-test for the hands-free loop (PLAN integration milestone I1, item 5).

Acts as the person: the P15 speaker asks spoken questions (local Kokoro voice), Cammy's
own microphone hears them, Gemini Live answers through Cammy's speaker, and the P15 mic
plus local Whisper confirm the spoken answer. Each check needs (a) Gemini heard the
question, (b) Gemini replied, (c) the P15 heard that reply acoustically, and for the
vision question (d) the look tool returned a frame and the answer names something in it.

Plays sound on the P15 and Cammy: announce before running. Writes a JSON report with
transcripts of the synthetic exchange to private/live-test/. Usage:
  tools/live_selftest.py [--level normal] [--extra-args ...]
"""
import argparse
import json
import re
import signal
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'private/live-test'
VOICE_PY = ROOT.parent / 'voice-keys/.venv/bin/python'
LIVE_PY = ROOT / '.venv-live/bin/python'
WORKER = ROOT / 'host/conversation_worker.py'

QUESTIONS = [
    {'ask': 'Hi Cammy! Can you hear me? Please say hello back.', 'heard': ['hear', 'hello', 'cammy'], 'expect': None},
    {'ask': 'Cammy, what is two plus three?', 'heard': ['two', 'three', 'plus', '2', '3'], 'expect': ['five', '5']},
    {'ask': 'Cammy, what do you see in front of you right now?', 'heard': ['see', 'front'],
     # Scene words only: "camera" would also match "my camera isn't working".
     'expect': ['couch', 'sofa', 'leather', 'cushion', 'cushions', 'poly', 'conferencing', 'conference', 'soundbar', 'webcam'],
     'look': True},
]
# Resumption check (run with --resume): every question after the first waits for a fresh
# Gemini connection (the loop runs with --rotate), so the recall answer proves the context
# crossed a reconnect.
RESUME_QUESTIONS = [
    {'ask': 'Cammy, please remember this for me: my favourite colour is green.',
     'heard': ['remember', 'colour', 'color', 'green', 'favourite', 'favorite'], 'expect': None},
    {'ask': 'Cammy, what is my favourite colour?', 'heard': ['colour', 'color', 'favourite', 'favorite'],
     'expect': ['green'], 'after_resume': True},
    dict(QUESTIONS[2], after_resume=True),
]
WORD = re.compile(r"[a-z0-9']+")


def words(text):
    return WORD.findall((text or '').lower())


def tts(text):
    raw = subprocess.run([str(VOICE_PY), str(WORKER), 'tts'], input=text.encode(), capture_output=True,
                         timeout=120, check=True).stdout
    path = OUT / ('q-%d.wav' % (abs(hash(text)) % 10 ** 8))
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(raw)
    return path, len(raw) / 48000


def stt(pcm16):
    x = np.frombuffer(pcm16, '<i2')
    loud = np.where(np.abs(x) > 300)[0]
    if not len(loud):
        return ''
    x = x[max(0, loud[0] - 3200):][:127000]
    if len(x) < 5600:
        return ''
    out = subprocess.run([str(VOICE_PY), str(WORKER), 'stt'], input=x.astype('<i2').tobytes(),
                         capture_output=True, timeout=120)
    try:
        return json.loads(out.stdout.decode().strip().splitlines()[-1])['text']
    except (ValueError, IndexError, KeyError):
        return ''


def p15(kind):
    out = subprocess.run(['pw-cli', 'ls', 'Node'], capture_output=True, text=True).stdout
    return next(t for t in out.split('"') if t.startswith('alsa_%s.usb-Poly' % kind))


class Live:
    """Runs host/cammy_gemini_live.py and timestamps its stdout lines."""

    def __init__(self, extra, minutes=8):
        self.lines = []
        self.proc = subprocess.Popen([str(LIVE_PY), '-u', str(ROOT / 'host/cammy_gemini_live.py'), '--minutes', str(minutes)] + extra,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.rstrip()
            if line.startswith(('[', 'heard:', 'cammy:', 'Traceback')) or 'Error' in line:
                self.lines.append((time.time(), line))
                print('  live| ' + line[:160], flush=True)

    def wait_for(self, prefix, since, timeout):
        end = time.time() + timeout
        while time.time() < end:
            for t, line in self.lines:
                if t >= since and line.startswith(prefix):
                    return line
            if self.proc.poll() is not None:
                return None
            time.sleep(0.2)
        return None

    def since(self, t0):
        return [line for t, line in self.lines if t >= t0]

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--volume', type=float, default=0.7, help='P15 playback volume for the questions')
    ap.add_argument('--listen', type=float, default=16.0, help='seconds to record each answer')
    ap.add_argument('--resume', action='store_true', help='check context survives Gemini reconnects')
    ap.add_argument('--rotate', type=float, default=20, help='with --resume: seconds per Gemini connection; 0 waits for the real 10-minute go_away')
    ap.add_argument('extra', nargs='*', help='extra args for cammy_gemini_live.py (after --)')
    args = ap.parse_args()
    OUT.mkdir(mode=0o700, exist_ok=True)
    sink, source = p15('output'), p15('input')
    questions = RESUME_QUESTIONS if args.resume else QUESTIONS
    asks = [tts(q['ask']) for q in questions]
    natural = args.resume and not args.rotate
    live = Live(args.extra + (['--rotate', str(args.rotate)] if args.resume and args.rotate else []),
                minutes=14 if natural else 8)
    report = {'started': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'results': []}
    try:
        if not live.wait_for('[live] ready', 0, 60):
            raise SystemExit('live loop did not start')
        time.sleep(2)
        prev = time.time()
        for q, (wav, seconds) in zip(questions, asks):
            resumed = None
            # Natural mode has one reconnect (the server's go_away near 10 min): only the
            # recall question waits for it.
            wait_resume = q.get('after_resume') and not (natural and q.get('look'))
            if wait_resume:
                resumed = live.wait_for('[live] resumed', prev, 720 if natural else 90)
                time.sleep(1)
            t0 = prev = time.time()
            print('ask: ' + q['ask'], flush=True)
            subprocess.run(['pw-play', '--target', sink, '--volume', str(args.volume), str(wav)], timeout=30)
            raw_path = OUT / 'answer.raw'
            with open(raw_path, 'wb') as sink_file:  # a file, not a pipe: an unread pipe fills in 2 s
                rec = subprocess.Popen(['pw-record', '--raw', '--target', source, '--rate', '16000', '--channels', '1',
                                        '--format', 's16', '-'], stdout=sink_file, stderr=subprocess.DEVNULL)
                time.sleep(args.listen)
                rec.terminate(); rec.wait(5)
            pcm = raw_path.read_bytes(); raw_path.unlink()
            heard_line = next((l for l in live.since(t0) if l.startswith('heard:')), '')
            said_line = next((l for l in live.since(t0) if l.startswith('cammy:')), '')
            tool = [l for l in live.since(t0) if l.startswith('[tool]')]
            acoustic = stt(pcm)
            said_words = set(words(said_line[6:]))
            overlap = len(said_words & set(words(acoustic)))
            checks = {
                'gemini_heard_question': any(k in words(heard_line) for k in q['heard']),
                'gemini_replied': bool(said_words),
                'p15_heard_reply': overlap >= min(3, max(1, len(said_words) // 3)),
            }
            if q['expect']:
                checks['answer_relevant'] = any(k in said_words for k in q['expect'])
            if wait_resume and not natural:
                checks['resumed_with_handle'] = bool(resumed) and 'kept' in resumed
            elif wait_resume:
                # Natural mode: the server decides when to send go_away (none came in 13 min
                # on 2026-11-16), so a missing reconnect is recorded, not failed; a reconnect
                # that lost the handle still fails.
                if resumed and 'kept' not in resumed:
                    checks['resumed_with_handle'] = False
                report['natural_reconnect'] = resumed or 'none within the wait'
            if q.get('look'):
                checks['look_tool_frame'] = any(re.search(r'-> frame \d+ bytes', l) for l in tool)
            result = {'question': q['ask'], 'heard': heard_line[6:].strip(), 'cammy': said_line[6:].strip(),
                      'p15_whisper': acoustic, 'word_overlap': overlap, 'tool': tool, 'checks': checks,
                      'passed': all(checks.values())}
            report['results'].append(result)
            print('  -> %s %s | p15 heard: %s' % ('PASS' if result['passed'] else 'FAIL', checks, acoustic[:100]), flush=True)
            time.sleep(2)
    finally:
        live.stop()
        report['live_tail'] = [l for _, l in live.lines if l.startswith(('[live]', '[gemini]'))]
        report['passed'] = bool(report['results']) and all(r['passed'] for r in report['results'])
        path = OUT / ('selftest-%s.json' % time.strftime('%Y%m%dT%H%M%S'))
        path.write_text(json.dumps(report, indent=1))
        print('SELFTEST %s: %d/%d passed; report %s' % ('PASS' if report['passed'] else 'FAIL',
              sum(r['passed'] for r in report['results']), len(questions), path), flush=True)


if __name__ == '__main__':
    main()
