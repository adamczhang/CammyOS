"""Official MCP SDK stdio tools for agents -> Cammy Control Center (C3) local API.

Default (no arguments): the CCC server. Every tool is a cammy_c3_* call through the authenticated
C3Client; C3 holds Cammy's one lease, so this server never opens a session, camera or microphone
of its own and never falls back to the old agent service.

`cammy_mcp.py --legacy`: the old finite-session tools (cammy_mcp_legacy.py) and nothing else.
The two tool sets are never offered by one server.
"""
import argparse
import base64
import json
import math
import time
from datetime import datetime

from mcp import MCPError
from mcp.server import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent

from c3_client import C3Client, C3Error

MAX_RESPONSE_BYTES = 64_000       # JSON text budget for list-like replies (transcript, brain history)
MAX_ENTRY_CHARS = 4_000           # one transcript line or brain turn
MAX_DETAIL_CHARS = 300            # provider detail/recovery strings
MAX_IMAGE_BYTES = 4_000_000       # one JPEG
BRAIN_HISTORY_MAX = 50

INSTRUCTIONS = (
    'Cammy Control Center (C3) holds Cammy\'s one control lease; these tools ask C3 and never open a '
    'session, camera or microphone of their own. cammy_c3_look is LIVE: the newest preview frame, '
    'labelled with the time this server received it (C3 reports no capture time). '
    'cammy_c3_look_back_history/cammy_c3_look_back_frame are STORED frames of the selected profile '
    'only, with their stored times: never present them as live. cammy_c3_say and cammy_c3_message '
    'go to Cammy\'s spoken voice; cammy_c3_brain_message is a private, unspoken turn for the resident '
    'brain profile you name. Never infer physical motion or cleanup from acknowledgements. Legacy '
    'finite-session tools exist only in the separate `cammy_mcp.py --legacy` server.')


def result(value):
    return CallToolResult(content=[TextContent(type='text', text=json.dumps(value))], structuredContent=value)


def failure(message, code=503):
    return CallToolResult(content=[TextContent(type='text', text=json.dumps(
        {'error': str(message)[:MAX_DETAIL_CHARS], 'code': code}))], isError=True)


def strict_fields(allowed):
    """MCP middleware: refuse arguments a tool does not declare (before the SDK's own validation)."""
    async def middleware(ctx, call_next):
        if ctx.method == 'tools/call' and isinstance(ctx.params, dict):
            name = ctx.params.get('name')
            arguments = ctx.params.get('arguments', {})
            if name in allowed and (not isinstance(arguments, dict) or arguments.keys() - allowed[name]):
                raise MCPError(code=-32602, message='Unexpected Cammy tool arguments')
        return await call_next(ctx)
    return middleware


def iso(t):
    return None if t is None else datetime.fromtimestamp(t).astimezone().isoformat(timespec='milliseconds')


def clip(text, limit=MAX_ENTRY_CHARS):
    text = text if isinstance(text, str) else ('' if text is None else str(text))
    return (text, False) if len(text) <= limit else (text[:limit], True)


def newest_within(entries, budget=MAX_RESPONSE_BYTES):
    """Keep the newest entries whose JSON fits in budget; returns (kept oldest-first, dropped count)."""
    kept, used = [], 0
    for entry in reversed(entries):
        size = len(json.dumps(entry)) + 2
        if used + size > budget:
            break
        kept.append(entry)
        used += size
    return kept[::-1], len(entries) - len(kept)


def image(raw):
    if not isinstance(raw, (bytes, bytearray)) or not raw.startswith(b'\xff\xd8'):
        raise C3Error(502, 'C3 did not return a JPEG')
    if len(raw) > MAX_IMAGE_BYTES:
        raise C3Error(502, 'C3 frame is larger than %d bytes' % MAX_IMAGE_BYTES)
    return ImageContent(type='image', data=base64.b64encode(raw).decode(), mimeType='image/jpeg')


def create_server(*, c3=None):
    c3 = c3 or C3Client()
    server = MCPServer('Cammy', version='0.2.0', instructions=INSTRUCTIONS)

    def guarded(fn):
        try:
            return fn()
        except C3Error as e:
            return failure(str(e), e.code)
        except Exception as e:  # noqa: BLE001 - a tool reply, never a server crash
            return failure('C3 call failed: %s' % str(e)[:120])

    def c3call(fn, *args):
        return guarded(lambda: result(fn(*args)))

    def active_profile(expected):
        """The resident brain's active profile id, which must be the one the agent named."""
        state = c3.brain()
        active = state.get('active_profile')
        if active is None:
            raise C3Error(409, 'no resident brain profile is selected')
        if active != expected:
            raise C3Error(409, 'profile %r is not the active resident brain profile (active: %r)'
                          % (expected, active))
        return state

    # ---- existing cammy_c3_* contracts ----------------------------------------------------
    @server.tool()
    def cammy_c3_status() -> CallToolResult:
        """Cammy Control Center summary: connected, voice awake/asleep, duplex, face follow, live view, last error. Read-only."""
        return c3call(c3.summary)

    @server.tool()
    def cammy_c3_look() -> CallToolResult:
        """LIVE: the newest JPEG from Cammy's camera through C3 (C3 must be running with preview). The image comes first, then metadata with source='live' and the time this server received it; C3 reports no capture time."""
        def look():
            raw = c3.look()
            received = time.time()
            picture = image(raw)
            meta = {'source': 'live', 'received_at': round(received, 3), 'received_at_iso': iso(received),
                    'capture_time': None, 'bytes': len(raw)}
            return CallToolResult(content=[picture, TextContent(type='text', text=json.dumps(meta))])
        return guarded(look)

    @server.tool()
    def cammy_c3_say(text: str) -> CallToolResult:
        """Cammy says this to Adam, in its own voice and words (Gemini Live through C3)."""
        return c3call(c3.say, text)

    @server.tool()
    def cammy_c3_message(text: str) -> CallToolResult:
        """Send a user-role turn to Cammy's voice (wakes it); Cammy answers out loud. Not the resident brain (see cammy_c3_brain_message)."""
        return c3call(c3.message, text)

    @server.tool()
    def cammy_c3_transcript(n: int = 20) -> CallToolResult:
        """The newest n lines of the C3 conversation transcript (1..400). Long lines and replies over the size budget are cut, newest kept; 'truncated' says so."""
        def read():
            entries = (c3.transcript(n) or {}).get('entries') or []
            lines, cut = [], False
            for entry in entries:
                if isinstance(entry, dict) and 'text' in entry:
                    text, short = clip(entry.get('text'))
                    entry = {**entry, 'text': text, **({'truncated': True} if short else {})}
                    cut |= short
                lines.append(entry)
            lines, dropped = newest_within(lines)
            return result({'entries': lines, 'truncated': cut or dropped > 0, 'dropped': dropped})
        return guarded(read)

    @server.tool()
    def cammy_c3_follow(on: bool = True) -> CallToolResult:
        """Stock face follow on/off (the DOWN-button toggle, state checked); the screen shows the live view while it follows."""
        return c3call(c3.follow, on)

    @server.tool()
    def cammy_c3_show_camera(on: bool = True) -> CallToolResult:
        """Cammy's screen shows the live camera view (true) or its face (false)."""
        return c3call(c3.camera_view, on)

    @server.tool()
    def cammy_c3_look_around() -> CallToolResult:
        """Sweep the camera from end stop to end stop (about 350 degrees, about 20 s) taking 6 photos; the photos go to Cammy's voice, which describes them."""
        return c3call(c3.look_around)

    @server.tool()
    def cammy_c3_turn_around() -> CallToolResult:
        """Press Cammy's turn-180 button (face Adam / face forward). Stock also switches face follow off."""
        return c3call(c3.turn_around)

    @server.tool()
    def cammy_c3_capture(kind: str = 'photo') -> CallToolResult:
        """photo or video: Cammy shows the live view and Adam presses the red button; the result comes back to Cammy's voice."""
        return c3call(c3.capture, kind)

    # ---- read-only provider status ----------------------------------------------------------
    @server.tool()
    def cammy_c3_providers() -> CallToolResult:
        """Read-only resident provider status (Codex, Claude Code): installed, sign-in kind, last check. Never signs in, runs a provider test or refreshes; credentials are never returned."""
        def read():
            raw = c3.providers() or {}
            rows = []
            for row in raw.get('providers') or []:
                if isinstance(row, dict):
                    rows.append({'id': row.get('id'), 'name': row.get('name'), 'installed': row.get('installed'),
                                 'auth': row.get('auth'), 'check': row.get('check'),
                                 'detail': clip(row.get('detail'), MAX_DETAIL_CHARS)[0],
                                 'recovery': clip(row.get('recovery'), MAX_DETAIL_CHARS)[0]})
            value = {'providers': rows}
            if isinstance(raw.get('brain'), dict):
                brain = raw['brain']
                value['brain'] = {'available': brain.get('available'), 'code': brain.get('code'),
                                  'error': clip(brain.get('error'), MAX_DETAIL_CHARS)[0]}
            return result(value)
        return guarded(read)

    # ---- resident brain ---------------------------------------------------------------------
    @server.tool()
    def cammy_c3_brain() -> CallToolResult:
        """Resident brain status: active profile id, profiles (id, name, provider), whether a turn is pending. Read-only."""
        def read():
            state = c3.brain() or {}
            return result({'backend': state.get('backend'), 'active_profile': state.get('active_profile'),
                           'profiles': [{k: p.get(k) for k in ('id', 'name', 'provider')}
                                        for p in state.get('profiles') or [] if isinstance(p, dict)],
                           'pending': state.get('pending')})
        return guarded(read)

    @server.tool()
    def cammy_c3_brain_history(profile: str, n: int = 10) -> CallToolResult:
        """The newest n (1..50) resident brain turns of `profile`, which must be the ACTIVE profile (cammy_c3_brain); other profiles are refused. Stored history, not live; turns carry no timestamps. Long texts are cut ('truncated')."""
        def read():
            count = max(1, min(BRAIN_HISTORY_MAX, int(n)))
            active_profile(profile)
            entries = (c3.brain_history(profile) or {}).get('entries') or []
            active_profile(profile)       # still the same profile after the read
            turns, cut = [], False
            for turn in entries[-count:]:
                if not isinstance(turn, dict):
                    continue
                text, short = clip(turn.get('text'))
                turns.append({**{k: turn.get(k) for k in ('id', 'job_id', 'role', 'status') if k in turn},
                              'text': text, **({'truncated': True} if short else {})})
                cut |= short
            turns, dropped = newest_within(turns)
            return result({'source': 'history', 'profile': profile, 'entries': turns,
                           'truncated': cut or dropped > 0, 'dropped': dropped})
        return guarded(read)

    @server.tool()
    def cammy_c3_brain_message(profile: str, text: str) -> CallToolResult:
        """Queue a private, unspoken turn for the resident brain. `profile` must be the ACTIVE profile (cammy_c3_brain), else it is refused; one turn at a time (409 while busy). Read the answer later with cammy_c3_brain_history."""
        return c3call(c3.brain_message, profile, text)

    # ---- Look Back: stored camera history of the selected profile ---------------------------
    @server.tool()
    def cammy_c3_look_back_history() -> CallToolResult:
        """STORED camera history metadata of the selected profile: profile id/name, frame count, oldest/newest stored times (Unix s and ISO). No images. Pass the profile id to cammy_c3_look_back_frame."""
        def read():
            h = c3.preview_history() or {}
            now = time.time()
            return result({'source': 'history', 'profile': h.get('profile'), 'profile_name': h.get('profile_name'),
                           'frames': h.get('frames'), 'bytes': h.get('bytes'), 'capacity_bytes': h.get('capacity_bytes'),
                           'oldest': h.get('oldest'), 'oldest_iso': iso(h.get('oldest')),
                           'newest': h.get('newest'), 'newest_iso': iso(h.get('newest')),
                           'checked_at': round(now, 3), 'checked_at_iso': iso(now)})
        return guarded(read)

    @server.tool()
    def cammy_c3_look_back_frame(profile: str, at: float) -> CallToolResult:
        """STORED JPEG nearest Unix time `at` from the camera history of `profile`, which must be the active profile id from cammy_c3_look_back_history; any other profile is refused (409). 404 = a gap or overwritten. Metadata (source='history', stored frame time) comes first, then the image. Not a live view."""
        def read():
            if not isinstance(profile, str) or not profile.strip():
                raise C3Error(400, 'profile: the active profile id from cammy_c3_look_back_history')
            if not math.isfinite(at) or at <= 0:
                raise C3Error(400, 'at: Unix seconds')
            found = c3.preview_frame(profile, at)
            if found.get('profile') != profile:
                raise C3Error(409, 'the frame belongs to another profile')
            picture = image(found['jpeg'])
            now = time.time()
            meta = {'source': 'history', 'profile': profile, 'requested_at': at, 'frame_time': found['time'],
                    'frame_time_iso': iso(found['time']), 'offset_s': round(found['time'] - at, 3),
                    'age_s': round(now - found['time'], 3), 'bytes': len(found['jpeg'])}
            return CallToolResult(content=[TextContent(type='text', text=json.dumps(meta)), picture])
        return guarded(read)

    allowed = {'cammy_c3_status': set(), 'cammy_c3_look': set(), 'cammy_c3_say': {'text'},
               'cammy_c3_message': {'text'}, 'cammy_c3_transcript': {'n'}, 'cammy_c3_follow': {'on'},
               'cammy_c3_show_camera': {'on'}, 'cammy_c3_look_around': set(), 'cammy_c3_turn_around': set(),
               'cammy_c3_capture': {'kind'}, 'cammy_c3_providers': set(), 'cammy_c3_brain': set(),
               'cammy_c3_brain_history': {'profile', 'n'}, 'cammy_c3_brain_message': {'profile', 'text'},
               'cammy_c3_look_back_history': set(), 'cammy_c3_look_back_frame': {'profile', 'at'}}
    server.middleware.append(strict_fields(allowed))
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description='Cammy MCP stdio server (default: Cammy Control Center tools).')
    parser.add_argument('--legacy', action='store_true',
                        help='serve ONLY the old finite-session tools on the agent service (no cammy_c3_* tools)')
    args = parser.parse_args(argv)
    if args.legacy:
        from cammy_mcp_legacy import create_legacy_server
        create_legacy_server().run(transport='stdio')
    else:
        create_server().run(transport='stdio')


if __name__ == '__main__':
    main()
