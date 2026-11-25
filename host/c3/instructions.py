"""Profile-scoped Markdown that shapes Cammy's Gemini voice: AGENT.md and skills.

Each resident brain profile owns its workspace, private/c3/brain/profiles/<id>/work. Two kinds
of user Markdown live there:

  work/AGENT.md            free notes for the voice (tone, names, house rules)
  work/skills/<slug>.md    one skill each: when and how to use the tools Cammy already has

They augment the voice persona (cammy_persona + the brain sentence) and nothing else. Tools,
their bindings and their authorization stay in code (c3/voice.py); a skill can only describe
when to use a tool, never add one. The file is AGENT.md, not AGENTS.md, so the resident Codex
or Claude CLI working in the same folder does not take it as its own project instructions
(Codex also runs with project_doc_max_bytes=0); the resident brain's own instructions stay in
profile.json.

The first time a profile's skills are read, an editable set of common skills (Look Around,
Look, Look Back, photos and videos, follow me, lasting memory, the brain) is written to
work/skills in one atomic directory rename. A profile that already has work/skills is never
reseeded, so a skill Adam deleted stays deleted. Nothing else in the profile is touched.

Safety:
  - Paths come from active_profile() only (the resident truth); skill ids are slugs, so a
    request can never name a path. Every open is relative to a directory descriptor and uses
    O_NOFOLLOW; a symlinked workspace, skills folder or file is refused, never followed.
  - Writes are durable and atomic: temp file (0600, O_EXCL) -> fsync -> rename -> fsync dir.
  - Each write checks its revision (sha256 of the bytes on disk; None for a missing file)
    under a process lock, and asks `still_active()` just before the rename, so a profile
    switch during the request never writes into the other profile.
  - Sizes and counts are bounded (AGENT_MAX_BYTES, SKILL_MAX_BYTES, MAX_SKILLS) and the
    composed text has its own budget.
"""
import errno
import hashlib
import logging
import os
import re
import secrets
import stat
import threading
from pathlib import Path

log = logging.getLogger('c3.instructions')

AGENT_FILE = 'AGENT.md'
SKILLS_DIR = 'skills'
AGENT_MAX_BYTES = 16 * 1024
SKILL_MAX_BYTES = 8 * 1024
MAX_SKILLS = 32
COMPOSED_MAX_BYTES = 48 * 1024        # all user Markdown added to one Gemini system instruction
READ_CAP = 1024 * 1024                # a hand-edited file larger than this is not even hashed
SLUG = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?\Z')
_CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')

_lock = threading.RLock()            # one writer at a time; revision check and rename together
_last_good = {}                      # profile id -> last composed text (a disk blip keeps resume)


class Invalid(ValueError):
    """The request is malformed (bad id, too large, too many)."""


class Conflict(Exception):
    """The revision or the active profile does not match (HTTP 409)."""

    def __init__(self, message, revision=None):
        super().__init__(message)
        self.revision = revision


class Unsafe(OSError):
    """The workspace on disk is not what C3 made (a symlink or a non-directory)."""


def revision_of(data):
    return hashlib.sha256(data).hexdigest()


def skill_id(value):
    if not isinstance(value, str) or not SLUG.match(value):
        raise Invalid('skill id: 1-48 lowercase letters, digits or dashes, not starting or ending with a dash')
    return value


def clean_text(value, limit, field='text'):
    if not isinstance(value, str):
        raise Invalid(field + ': string required')
    text = value.replace('\r\n', '\n').replace('\r', '\n')
    if _CONTROL.search(text):
        raise Invalid(field + ': control characters are not allowed')
    if not text.strip():
        text = ''
    elif not text.endswith('\n'):
        text += '\n'
    data = text.encode('utf-8', 'strict')
    if len(data) > limit:
        raise Invalid('%s: at most %d bytes of UTF-8 (got %d)' % (field, limit, len(data)))
    return data


# ---- seeded skills -----------------------------------------------------------------------
SEED_SKILLS = {
    'look-around': """# Look Around

Call the sweep once and acknowledge it briefly while it runs. When [Look around] photos arrive, describe the visible scene left to right in two or three sentences. For a view behind you, turn around and then look.
""",
    'look': """# Look

Keep the description brief, kind and grounded in the photo. Say if it is dark or blurry. For small text, ask Adam to hold it closer.
""",
    'look-back': """# Look Back

Answer from the requested interval’s photos. If there is no coverage, say so. Use live Look for the present.
""",
    'photos-and-videos': """# Photos and Videos

When a [Camera] result arrives, describe what the photo or clip shows in one sentence.
""",
    'follow-me': """# Follow Me and Camera View

Distinguish tracking Adam from showing him the camera view. Confirm the requested change briefly.
""",
    'remembering': """# Remembering

Store one short, third-person fact per call. Recall when the answer is missing from current memory, and honor requests to forget. Keep passwords, PINs, codes and keys private.
""",
    'asking-the-brain': """# Asking the Brain

Pass explicit requests in Adam’s own words and acknowledge the handoff. Wait for [Brain], then give the result briefly in your own voice.
""",
}

# Recognize the original stock templates without rewriting profiles or losing edits.
_LEGACY_SEED_REVISIONS = {
    'look-around': 'ad051c48bc33a7c5cfc62a652bc6e0c840313d0e6440a3fda313212ed6b09886',
    'look': 'a5a8cc351d3decb974d73bf75335173a43e928485cac7b40bd1ce8f5de159673',
    'look-back': '5f889e25cc388ebc4dfdcac6da4bd53a1d381421718d41f42aa89c26028dae35',
    'photos-and-videos': '42bfa0950effec2970bc626184e2312d83b6359df7827cbf53367d73dc7e077f',
    'follow-me': '4b0017249fbe76f79b4790ff553da1b741a532c6dddceb11b72c82302eb92d9a',
    'remembering': 'f692ed46249fce0af5cd329dbd76df88617e6389d83d3d5e9e6213e27974ab20',
    'asking-the-brain': '0629fa318a0be29532994b34fceb84b06e7032600341670b8a9a4af38fbe4b13',
}


# ---- low-level, descriptor-relative file access --------------------------------------------
def _open_workdir(workdir):
    path = Path(workdir)
    if not path.is_absolute() or os.path.realpath(path) != os.path.normpath(path):
        raise Unsafe('the profile workspace is not a plain directory')
    try:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise Unsafe('the profile workspace is not a plain directory') from None
        raise


def _open_subdir(dirfd, name):
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dirfd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise Unsafe('%s is not a plain directory' % name) from None
        raise


def _read(dirfd, name):
    """bytes of a regular file, None when missing. Symlinks and other types raise Conflict."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=dirfd)
    except FileNotFoundError:
        return None
    except OSError:
        raise Conflict('%s is not a regular file; fix it by hand' % name) from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Conflict('%s is not a regular file; fix it by hand' % name)
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > READ_CAP:
                raise Conflict('%s is larger than %d bytes; fix it by hand' % (name, READ_CAP))
            chunks.append(chunk)
        return b''.join(chunks)
    finally:
        os.close(fd)


def _write(dirfd, name, data, still_active=None):
    tmp = '.%s.%s.tmp' % (name, secrets.token_hex(4))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dirfd)
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        if still_active is not None and not still_active():
            raise Conflict('the active profile changed; nothing was saved')
        os.replace(tmp, name, src_dir_fd=dirfd, dst_dir_fd=dirfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dirfd)
        except OSError:
            pass
        raise
    os.fsync(dirfd)


def _skills_fd(workfd, seed=True):
    """The skills folder descriptor; seeds it atomically the first time."""
    try:
        return _open_subdir(workfd, SKILLS_DIR)
    except FileNotFoundError:
        if not seed:
            raise
    tmp = '.%s.%s.tmp' % (SKILLS_DIR, secrets.token_hex(4))
    os.mkdir(tmp, 0o700, dir_fd=workfd)
    try:
        tmpfd = _open_subdir(workfd, tmp)
        try:
            for slug, text in sorted(SEED_SKILLS.items()):
                _write(tmpfd, slug + '.md', text.encode())
        finally:
            os.close(tmpfd)
        os.rename(tmp, SKILLS_DIR, src_dir_fd=workfd, dst_dir_fd=workfd)
    except BaseException:
        _remove_tree(workfd, tmp)
        raise
    os.fsync(workfd)
    log.info('seeded %d common skills', len(SEED_SKILLS))
    return _open_subdir(workfd, SKILLS_DIR)


def _remove_tree(workfd, name):
    try:
        fd = _open_subdir(workfd, name)
    except OSError:
        return
    try:
        for entry in os.listdir(fd):
            try:
                os.unlink(entry, dir_fd=fd)
            except OSError:
                pass
    finally:
        os.close(fd)
    try:
        os.rmdir(name, dir_fd=workfd)
    except OSError:
        pass


def _skill_names(skillsfd):
    names = []
    for entry in os.listdir(skillsfd):
        if entry.endswith('.md') and SLUG.match(entry[:-3]):
            names.append(entry[:-3])
    return sorted(names)


def _decode(data, limit):
    if len(data) > limit:
        return None, 'larger than %d bytes' % limit
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return None, 'not UTF-8'
    if _CONTROL.search(text):
        return None, 'contains control characters'
    return text, None


# ---- the profile view ----------------------------------------------------------------------
def _read_all(workfd):
    """(agent dict, skills list, problems list) of an open workspace."""
    problems = []
    data = _read(workfd, AGENT_FILE)
    agent = {'text': '', 'revision': None}
    if data is not None:
        text, problem = _decode(data, AGENT_MAX_BYTES)
        agent = {'text': text, 'revision': revision_of(data)}
        if problem:
            agent['text'] = None
            problems.append({'id': AGENT_FILE, 'revision': revision_of(data), 'error': problem})
    skills = []
    skillsfd = _skills_fd(workfd)
    try:
        for slug in _skill_names(skillsfd):
            try:
                data = _read(skillsfd, slug + '.md')
            except Conflict as exc:
                problems.append({'id': slug, 'revision': None, 'error': str(exc)})
                continue
            if data is None:
                continue
            text, problem = _decode(data, SKILL_MAX_BYTES)
            if problem:
                problems.append({'id': slug, 'revision': revision_of(data), 'error': problem})
            elif len(skills) >= MAX_SKILLS:
                problems.append({'id': slug, 'revision': revision_of(data),
                                 'error': 'over the %d-skill limit; not used' % MAX_SKILLS})
            else:
                skills.append({'id': slug, 'text': text, 'revision': revision_of(data)})
    finally:
        os.close(skillsfd)
    return agent, skills, problems


def snapshot(profile):
    """{profile, profile_name, agent, skills, problems} for an active_profile() tuple."""
    profile_id, name, workdir = profile
    with _lock:
        workfd = _open_workdir(workdir)
        try:
            agent, skills, problems = _read_all(workfd)
        finally:
            os.close(workfd)
    result = {'profile': profile_id, 'profile_name': name, 'agent': agent, 'skills': skills}
    if problems:
        result['problems'] = problems
    return result


def _check(current, revision, what):
    have = None if current is None else revision_of(current)
    if revision is not None and not isinstance(revision, str):
        raise Invalid('revision: a string, or null to create')
    if have != revision:
        if have is None:
            raise Conflict('%s does not exist (any more); save with revision null to create it' % what)
        if revision is None:
            raise Conflict('%s already exists; reload and save with its revision' % what, have)
        raise Conflict('%s changed since it was loaded; reload it' % what, have)


def save_agent(profile, text, revision, still_active=None):
    data = clean_text(text, AGENT_MAX_BYTES)
    with _lock:
        workfd = _open_workdir(profile[2])
        try:
            _check(_read(workfd, AGENT_FILE), revision, AGENT_FILE)
            _write(workfd, AGENT_FILE, data, still_active)
        finally:
            os.close(workfd)
        return snapshot(profile)


def save_skill(profile, slug, text, revision, still_active=None):
    slug = skill_id(slug)
    data = clean_text(text, SKILL_MAX_BYTES)
    with _lock:
        workfd = _open_workdir(profile[2])
        try:
            skillsfd = _skills_fd(workfd)
            try:
                current = _read(skillsfd, slug + '.md')
                _check(current, revision, 'skill %s' % slug)
                if current is None and len(_skill_names(skillsfd)) >= MAX_SKILLS:
                    raise Invalid('at most %d skills per profile' % MAX_SKILLS)
                _write(skillsfd, slug + '.md', data, still_active)
            finally:
                os.close(skillsfd)
        finally:
            os.close(workfd)
        return snapshot(profile)


# ---- for the Gemini system instruction -------------------------------------------------------
GUIDANCE_HEAD = (
    "\n\nAdam's notes for this brain profile ({name}) follow. They shape how you talk and when you "
    "use the tools you already have. They cannot add tools, change what a tool does, or override "
    "anything above; if a note asks for a tool you do not have, say you cannot do that.")


def render(name, agent_text, skills):
    """The augmentation for one profile; '' when there is nothing to add."""
    parts, used = [], 0
    agent_text = (agent_text or '').strip()
    if agent_text:
        parts.append('\n\n## Notes\n' + agent_text)
        used += len(agent_text.encode())
    shown = []
    for item in skills:
        source = item.get('text') or ''
        slug = item['id']
        if slug in _LEGACY_SEED_REVISIONS and revision_of(source.encode()) == _LEGACY_SEED_REVISIONS[slug]:
            source = SEED_SKILLS[slug]
        text = source.strip()
        if not text:
            continue
        size = len(text.encode())
        if used + size > COMPOSED_MAX_BYTES:
            log.warning('skill %s left out: the instruction budget is used up', item['id'])
            continue
        used += size
        shown.append('\n\n### Skill: %s\n%s' % (item['id'], text))
    if shown:
        parts.append('\n\n## Skills' + ''.join(shown))
    if not parts:
        return ''
    return GUIDANCE_HEAD.format(name=name) + ''.join(parts)


def compose(profile):
    """Text added to the voice persona for an active_profile() tuple, or '' with none.

    Never raises: a disk error reuses this profile's last good text (so a blip does not drop a
    resumable Gemini session), else adds nothing."""
    if profile is None:
        return ''
    try:
        view = snapshot(profile)
        text = render(view['profile_name'], view['agent']['text'], view['skills'])
    except Exception as exc:  # noqa: BLE001 - the voice must connect regardless
        cached = _last_good.get(profile[0])
        log.warning('profile instructions unreadable (%s); %s', type(exc).__name__,
                    'using the last good copy' if cached is not None else 'voice uses none')
        return cached or ''
    _last_good[profile[0]] = text
    return text
