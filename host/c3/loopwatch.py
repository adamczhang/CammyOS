"""Event-loop stall watch for C3.

The portal drops the mic stream as a 'slow-reader' when the PC stops reading for about
a second (2026-11-23: streams lasted 7-60 s instead of 600 s during conversation tests,
and the reopen backoff then left Cammy deaf for up to 30 s). Mic reads hop through the
event loop, so anything that blocks the loop that long deafens Cammy.

A daemon thread watches a heartbeat the loop bumps every 100 ms. When the heartbeat is
older than STALL_S, it logs the loop thread's stack once per stall (what is blocking) and,
when the loop recovers, how long the stall lasted. Costs one thread waking 10 times a
second.
"""
import asyncio
import logging
import sys
import threading
import time
import traceback

log = logging.getLogger('c3.loopwatch')

BEAT_S = 0.1
STALL_S = 0.8


class LoopWatch:
    def __init__(self, stall_s=STALL_S, clock=time.monotonic):
        self.stall_s, self.clock = stall_s, clock
        self.beat = clock()
        self.loop_thread = None
        self.stalls = 0
        self.longest_s = 0.0
        self.last = None              # {'s': seconds, 'where': 'file:line func <- ...'}
        self._stop = threading.Event()

    async def run(self):
        self.loop_thread = threading.get_ident()
        thread = threading.Thread(target=self._watch, name='c3-loopwatch', daemon=True)
        thread.start()
        try:
            while True:
                self.beat = self.clock()
                await asyncio.sleep(BEAT_S)
        finally:
            self._stop.set()

    def where(self):
        frame = sys._current_frames().get(self.loop_thread)
        if frame is None:
            return '?'
        stack = traceback.extract_stack(frame)[-8:]
        return ' <- '.join(f'{f.filename.rsplit("/", 2)[-1]}:{f.lineno} {f.name}' for f in reversed(stack))

    def _watch(self):
        stalled_at, where = None, None
        while not self._stop.wait(BEAT_S):
            age = self.clock() - self.beat
            if stalled_at is None and age >= self.stall_s:
                stalled_at, where = self.beat, self.where()
                log.warning('event loop blocked %.1f s so far in: %s', age, where)
            elif stalled_at is not None and self.beat > stalled_at:
                took = round(self.beat - stalled_at, 2)
                self.stalls += 1
                self.longest_s = max(self.longest_s, took)
                self.last = {'s': took, 'where': where}
                log.warning('event loop was blocked %.2f s', took)
                stalled_at = None

    @property
    def state(self):
        return {'stalls': self.stalls, 'longest_s': self.longest_s, 'last': self.last}
