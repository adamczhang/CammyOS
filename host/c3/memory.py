"""Cammy's lasting memory for C3 (Adam, 2026-11-16; vault copy 2026-11-23): facts kept across conversations.

Two copies of every fact:
- private/c3/memory/facts.jsonl on this PC (git-ignored): the copy this C3 always has.
- One Markdown note per fact in the shared Agent-Memory vault, folder bots/cammy/
  (`cammy-fact-<id>.md`), so other agents and Adam (Obsidian) can see what Cammy was told and
  `forget` can delete it. Recall searches only that folder: nothing else in the vault reaches Gemini.
  Hindsight (the old bank `bot::cammy`) was retired on 2026-11-23.

Gemini sees the facts in its system instruction at every connection (`brief()`), and has
three tools: remember, recall_memory (vault notes plus local matches) and forget.
Secrets (passwords, PINs, codes, keys, long numbers) are refused before anything is kept.
"""
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger('c3.memory')

BRIEF_FACTS = 40          # newest facts in every system instruction
MAX_FACT = 300            # characters
SECRET = re.compile(r'pass\s*word|passcode|\bpin\b|api[\s_-]*key|\btoken\b|secret|credit\s*card|'
                    r'social\s*security|\bssn\b|security\s*code|one[\s-]*time\s*code|\b\d{6,}\b', re.I)
TOLD = re.compile(r'\s*\(Adam told Cammy on [0-9-]+\)\s*$')
SERVE_MOUNT = Path(os.environ.get('CAMMY_VAULT_MOUNT', Path.home() / 'vault-drive'))   # drive holding 'MEMORY VAULT'
VAULT_DIR = SERVE_MOUNT / 'MEMORY VAULT' / 'bots' / 'cammy'


class VaultCopy:
    """Cammy's facts as notes in the shared vault (bots/cammy/). Every call may fail (logged by the caller)."""

    def __init__(self, folder=VAULT_DIR):
        self.folder = Path(folder)
        if self.folder != VAULT_DIR and not self.folder.parent.is_dir():
            raise OSError('vault folder missing: %s' % self.folder.parent)

    def _check_mount(self, *, writing=False):
        if self.folder != VAULT_DIR:
            return
        if not SERVE_MOUNT.is_mount() or not self.folder.parent.is_dir():
            raise OSError('SERVE vault unavailable')
        if writing and (os.statvfs(SERVE_MOUNT).f_flag & os.ST_RDONLY):
            raise OSError('SERVE vault is read-only')

    def _path(self, doc_id):
        return self.folder / (re.sub(r'[^A-Za-z0-9_-]', '-', doc_id) + '.md')

    def retain(self, doc_id, text, when):
        self._check_mount(writing=True)
        self.folder.mkdir(parents=True, exist_ok=True)
        title = ' '.join(TOLD.sub('', text).split()[:10])
        body = ('---\ntitle: "%s"\ntype: cammy-fact\ntags: [cammy]\nstatus: active\ndate: %s\n'
                'source: Cammy voice conversation (C3)\n---\n%s\n') % (title.replace('"', "'"), when[:10], text)
        path = self._path(doc_id)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(body)
        os.replace(tmp, path)

    def delete(self, doc_id):
        self._check_mount(writing=True)
        try:
            self._path(doc_id).unlink()
        except FileNotFoundError:
            pass

    def recall(self, query, limit=10):
        self._check_mount()
        words = [w for w in re.findall(r'\w+', str(query or '').lower()) if len(w) > 2]
        scored = []
        for p in self.folder.glob('*.md'):
            text = p.read_text(errors='replace').split('\n---\n', 1)[-1].strip()
            score = sum(w in text.lower() for w in words)
            if score:
                scored.append((score, p.stat().st_mtime, text))
        scored.sort(reverse=True)
        return [t for _, _, t in scored[:limit]]


class Memory:
    def __init__(self, folder, shared=None, clock=time.time):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.folder / 'facts.jsonl'
        self.clock = clock
        self.lock = threading.Lock()
        self.shared = VaultCopy() if shared is None else shared
        self.stats = {'remembered': 0, 'forgotten': 0, 'recalls': 0, 'refused': 0, 'shared_errors': 0}

    # ---- local store ---------------------------------------------------------------
    def facts(self):
        """Live facts, oldest first."""
        out = {}
        try:
            lines = self.path.read_text().splitlines()
        except FileNotFoundError:
            return []
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get('forget'):
                out.pop(e['forget'], None)
            elif e.get('id') and e.get('text'):
                out[e['id']] = e
        return list(out.values())

    def _append(self, entry):
        with self.lock, open(self.path, 'a') as f:
            f.write(json.dumps(entry) + '\n')
        self.path.chmod(0o600)

    def _background(self, what, fn, *args):
        def run():
            try:
                fn(*args)
            except Exception as e:  # noqa: BLE001 - the local copy is kept regardless
                self.stats['shared_errors'] += 1
                log.warning('vault %s failed: %s: %s', what, type(e).__name__, str(e)[:160])
        threading.Thread(target=run, daemon=True).start()

    # ---- tools ---------------------------------------------------------------------
    def remember(self, text):
        """(ok, message)."""
        text = ' '.join(str(text or '').split())[:MAX_FACT]
        if not text:
            return False, 'nothing to remember'
        if SECRET.search(text):
            self.stats['refused'] += 1
            return False, 'not kept: it looks like a password, code or number that should stay private'
        if any(f['text'].lower() == text.lower() for f in self.facts()):
            return True, 'already remembered'
        now = self.clock()
        fid = '%d-%04x' % (now * 1000, hash(text) & 0xffff)
        when = time.strftime('%Y-%m-%dT%H:%M:%S%z', time.localtime(now))
        self._append({'id': fid, 'time': when, 'text': text})
        self.stats['remembered'] += 1
        if self.shared:
            self._background('retain', self.shared.retain, 'cammy-fact-' + fid,
                             '%s (Adam told Cammy on %s)' % (text, when[:10]), when)
        return True, 'remembered'

    def forget(self, about):
        """Removes every fact containing all the words of `about`. (count, removed texts)."""
        words = [w for w in re.findall(r'\w+', str(about or '').lower()) if len(w) > 2]
        if not words:
            return 0, []
        gone = [f for f in self.facts() if all(w in f['text'].lower() for w in words)]
        for f in gone:
            self._append({'forget': f['id'], 'time': time.strftime('%Y-%m-%dT%H:%M:%S%z')})
            if self.shared:
                self._background('delete', self.shared.delete, 'cammy-fact-' + f['id'])
        self.stats['forgotten'] += len(gone)
        return len(gone), [f['text'] for f in gone]

    def recall(self, query):
        """A list of short texts, best first (local file plus vault notes; milliseconds)."""
        self.stats['recalls'] += 1
        words = [w for w in re.findall(r'\w+', str(query or '').lower()) if len(w) > 2]
        local = [f['text'] for f in self.facts() if any(w in f['text'].lower() for w in words)]
        found = []
        if self.shared and query:
            try:
                found = self.shared.recall(str(query))
            except Exception as e:  # noqa: BLE001
                self.stats['shared_errors'] += 1
                log.warning('vault recall failed: %s: %s', type(e).__name__, str(e)[:160])
        seen, out = set(), []
        for t in local + found:
            t = TOLD.sub('', t)
            key = t.lower()[:80]
            if key not in seen:
                seen.add(key)
                out.append(t[:MAX_FACT])
        return out[:10]

    def brief(self):
        """For the system instruction: what Cammy remembers, newest last."""
        facts = self.facts()[-BRIEF_FACTS:]
        if not facts:
            return ''
        return ' '.join('(%s) %s' % (f['time'][:10], f['text']) for f in facts)
