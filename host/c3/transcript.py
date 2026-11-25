"""C3 transcript: every utterance, reply and tool use, as it happens.

One JSON line per entry in private/c3/transcript/<YYYY-MM-DD>.jsonl (git-ignored), files
older than `keep_days` are deleted. The last 400 entries stay in memory for the API, the
fresh-session summary and the brain, which gets new lines in batches (`since(mark)`)
instead of one transcript at exit, so a crash loses nothing.
"""
import collections
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

NAMES = {'adam': 'Adam', 'cammy': 'Cammy', 'tool': 'Tool', 'camera': 'Camera', 'brain': 'Brain', 'system': 'C3'}


class Transcript:
    def __init__(self, folder, keep_days=14, clock=time.time):
        self.folder, self.keep_days, self.clock = Path(folder), keep_days, clock
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.recent = collections.deque(maxlen=400)
        self.count = 0
        self.prune()
        self.earlier = self._load_earlier()

    def prune(self):
        cutoff = (datetime.fromtimestamp(self.clock()) - timedelta(days=self.keep_days)).strftime('%Y-%m-%d')
        for f in self.folder.glob('*.jsonl'):
            if f.stem < cutoff:
                f.unlink()

    def _load_earlier(self, hours=12, keep=60):
        """The last conversation before this C3 started, for a fresh session's summary after a
        restart. Kept apart from `recent`, so the brain is not sent it again."""
        cutoff, out = self.clock() - hours * 3600, []
        for f in sorted(self.folder.glob('*.jsonl'))[-2:]:
            try:
                for line in f.read_text().splitlines():
                    e = json.loads(line)
                    if e.get('t', 0) >= cutoff and e.get('who') in ('adam', 'cammy'):
                        out.append(e)
            except (OSError, ValueError):
                continue
        return out[-keep:]

    def add(self, who, text):
        text = (text or '').strip()
        if not text:
            return
        t = self.clock()
        entry = {'t': round(t, 3), 'who': who, 'text': text[:4000]}
        day = datetime.fromtimestamp(t).strftime('%Y-%m-%d')
        with open(self.folder / (day + '.jsonl'), 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        self.count += 1
        self.recent.append((self.count, entry))

    @staticmethod
    def line(entry):
        return '%s: %s' % (NAMES.get(entry['who'], entry['who']), entry['text'])

    def since(self, mark):
        """(lines added after mark, new mark). Entries that fell out of memory are skipped."""
        lines = [self.line(e) for n, e in self.recent if n > mark]
        return lines, self.count

    def recent_text(self, chars=1500):
        out = []
        for e in reversed(self.earlier + [e for _, e in self.recent]):
            if e['who'] not in ('adam', 'cammy'):
                continue
            out.append(self.line(e))
            if sum(len(x) for x in out) > chars:
                break
        return '\n'.join(reversed(out))[-chars:]
