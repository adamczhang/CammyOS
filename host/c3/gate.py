"""Talk gate for C3: is this utterance meant for Cammy?

Cammy's microphone hears the whole room. Gemini answers whatever reaches it, so speech
across the room, a TV or two people talking got "Did you say something?" replies (Adam,
2026-11-23). The gate sits between the listener and Voice and passes an utterance on
only when one of these holds:

  near     its onset is loud (>= near_dbfs): someone close, talking to Cammy. It goes
           on at once, streamed as before, with no added latency.
  engaged  Cammy is talking, about to talk, or talked within engaged_s, and the
           onset is at least follow_dbfs: a follow-up in a conversation.
  name     anything quieter is held on this PC until it ends, then a small local
           Whisper (c3/spotter.py) listens for "Cammy" or the face pack's name. Only
           then is it sent, start to end in one go.

A held utterance that never names Cammy is dropped and never leaves the PC, which
also saves API tokens and a reconnect for a sleeping session. Modes: smart (the
above), name (always the name unless engaged), open (everything passes, as before
2026-11-23). If the spotter is unavailable the gate fails open, as before.
"""
import asyncio
import logging
import re
import time
from collections import deque

from .listener import Event

log = logging.getLogger('c3.gate')

MODES = ('smart', 'name', 'open')
NEAR_DBFS = -38.0        # onset at or above: someone close is talking to Cammy
FOLLOW_DBFS = -52.0      # onset at or above, while engaged: a follow-up
FAINT_DBFS = -60.0       # a whole held utterance below this is not worth a spotter call
ENGAGED_S = 25.0         # a conversation stays open this long after Cammy or Adam last spoke
SPOT_MAX_S = 12.0        # the spotter hears at most the first 12 s of a held utterance
LEAD_IN_S = 0.3          # silence in front of the audio: Whisper drops a word said at 0 s
ALIASES = ('cammy', 'cammie', 'cami', 'camie', 'kammy', 'kami', 'cam me')


def name_pattern(names=()):
    words = sorted({w.lower() for w in (*ALIASES, *names) if w and w.strip()}, key=len, reverse=True)
    return re.compile(r'\b(?:' + '|'.join(re.escape(w).replace(r'\ ', r'\s+') for w in words) + r')\b', re.I)


class TalkGate:
    def __init__(self, forward, *, spotter=None, busy=lambda: False, clock=time.monotonic, mode='smart',
                 near_dbfs=NEAR_DBFS, follow_dbfs=FOLLOW_DBFS, faint_dbfs=FAINT_DBFS,
                 engaged_s=ENGAGED_S, names=lambda: (), rate=16000):
        if mode not in MODES:
            raise ValueError(f'talk gate mode must be one of {MODES}')
        self.forward, self.spotter, self.busy, self.clock = forward, spotter, busy, clock
        self.mode, self.near, self.follow, self.faint = mode, near_dbfs, follow_dbfs, faint_dbfs
        self.engaged_s, self.rate = engaged_s, rate
        self.names = names                # callable: the face pack's names, read per decision
        self.engaged_until = None
        self.live = False                 # an utterance is streaming through
        self.held = None                  # [start event, pcm bytearray] of an utterance being held
        self.pending = set()              # spotter decisions in flight
        self.recent = deque(maxlen=8)     # last decisions, for status
        self.counts = dict(passed=0, held=0, named=0, dropped=0, spotter_errors=0)

    # ---- engagement --------------------------------------------------------------
    def tick(self):
        """Called for every mic chunk: Cammy talking or waiting on a reply keeps the conversation open."""
        if self.busy():
            self.engage()

    def engage(self):
        self.engaged_until = self.clock() + self.engaged_s

    def engaged(self):
        return self.busy() or (self.engaged_until is not None and self.clock() < self.engaged_until)

    # ---- listener events -------------------------------------------------------------
    def feed(self, event):
        kind = event.kind
        if getattr(event, 'reason', None) == 'reset':
            self.held = None
            self.live = False
            self.forward(event)
            return
        if kind == 'start':
            self.held = None
            why = self._at_start(event)
            if why:
                self.live = True
                self._note('pass', why, event.level, event.noise)
                self.forward(event)
            else:
                self.held = [event, bytearray(event.pcm)]
        elif kind == 'audio':
            if self.live:
                self.forward(event)
            elif self.held is not None:
                self.held[1] += event.pcm
        elif kind == 'end':
            if self.live:
                self.live = False
                self.engage()
                self.forward(event)
            elif self.held is not None:
                start, pcm = self.held
                self.held = None
                self.counts['held'] += 1
                task = asyncio.get_running_loop().create_task(self._decide(start, bytes(pcm), event))
                self.pending.add(task)
                task.add_done_callback(self.pending.discard)

    def _at_start(self, ev):
        level = ev.level if ev.level is not None else 0.0
        if self.mode == 'open':
            return 'open'
        if self.engaged() and level >= self.follow:
            return 'engaged'
        if self.mode == 'smart' and level >= self.near:
            return 'near'
        return ''

    async def _decide(self, start, pcm, end):
        level = end.level if end.level is not None else start.level
        text, why = None, ''
        if self.mode == 'smart' and level is not None and level >= self.near:
            why = 'near'
        elif level is not None and level < self.faint:
            why = ''
        elif self.spotter is None:
            why = 'no-spotter'
        else:
            lead = bytes(int(LEAD_IN_S * self.rate) * 2)
            try:
                text = await self.spotter.transcribe(lead + pcm[:int(SPOT_MAX_S * self.rate) * 2])
            except Exception as exc:  # noqa: BLE001 - fail open: the gate must not deafen Cammy
                self.counts['spotter_errors'] += 1
                log.warning('name spotter failed (%s); passing the utterance', exc)
                why = 'no-spotter'
            else:
                if name_pattern(self.names()).search(text or ''):
                    why = 'name'
                    self.counts['named'] += 1
        reason = why or ('faint' if text is None else 'not-addressed')
        if why and (self.live or self.held is not None):
            why, reason = '', 'overlap'   # a newer utterance started meanwhile; this one is stale
        self._note('pass' if why else 'drop', reason, level, start.noise, end.seconds, text)
        if not why:
            return
        self.engage()
        self.forward(Event('start', pcm=pcm, level=start.level, noise=start.noise))
        self.forward(end)

    def _note(self, verdict, why, level, noise, seconds=None, text=None):
        self.counts['passed' if verdict == 'pass' else 'dropped'] += 1
        entry = {'at': round(time.time(), 1), 'verdict': verdict, 'why': why, 'level': level, 'noise': noise}
        if seconds is not None:
            entry['seconds'] = round(seconds, 1)
        if text is not None:
            entry['heard'] = text.strip()[:60]
        self.recent.append(entry)
        # one line per utterance: the levels are what the thresholds get tuned from
        log.info('talk gate %s (%s): level %s dBFS, noise %s%s', verdict, why, level, noise,
                 f', heard "{entry["heard"]}"' if 'heard' in entry else '')

    @property
    def state(self):
        return {'mode': self.mode, 'near_dbfs': self.near, 'follow_dbfs': self.follow, 'engaged_s': self.engaged_s,
                'engaged': self.engaged(), 'spotter': None if self.spotter is None else self.spotter.state,
                **self.counts, 'recent': list(self.recent)}
