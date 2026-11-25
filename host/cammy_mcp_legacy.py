"""LEGACY Cammy MCP tools: explicit finite sessions on the old PC agent service (port 8791).

Reached only through `cammy_mcp.py --legacy` (or create_legacy_server() here). It never offers
a cammy_c3_* tool, and the default CCC server never offers one of these: the two are not mixed.
These tools take their own finite control session through AgentClient, so they must not run while
C3 holds Cammy's lease; each keeps its old semantics exactly (quiet mode, actor/thread binding,
session ownership), all enforced by the agent service, never retargeted to C3.

Per-tool migration (what CCC offers instead, and what further C3 API work would be needed):

  cammy_status         No CCC equivalent. cammy_c3_status reads C3's state, but C3 has no quiet
                       mode and no per-actor sessions to report. Gap: a quiet/actor model in C3.
  cammy_device_status  Partly cammy_c3_status (connected, last error). Gap: gateway identity,
                       firmware and capability fields from the C3 body.
  cammy_open           Retired in CCC: C3 holds the one lease, agents never open their own.
  cammy_stop           Retired in CCC with cammy_open. Gap: a C3 per-agent cancel of queued work.
  cammy_frame          Closest is cammy_c3_look (live, no session). Gap: /v1/look returns no
                       capture time or boot/session id, so only the MCP receive time is given.
  cammy_pan            Gap: no finite left/right pan hold route; C3 offers only turn-around,
                       look-around and face follow.
  cammy_face           Gap: no LCD mood/expression route; C3 offers only camera-view on/off.
  cammy_say            Not cammy_c3_say: legacy is a short local-TTS phrase refused in quiet mode;
                       cammy_c3_say asks Gemini to say it in its own words, with no quiet check.
                       Gap: a verbatim, quiet-aware speak route in C3.
  cammy_listen         Gap: no bounded raw-microphone transcription route; cammy_c3_transcript
                       reads C3's conversation transcript, which is a different thing.
"""
import base64
import json
import os

from mcp.server import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent

from cammy_mcp import failure, result, strict_fields

INSTRUCTIONS = ('LEGACY mode: explicit finite sessions on the old Cammy agent service. Do not use while '
                'Cammy Control Center (C3) runs. Camera frames are live only when returned with fresh '
                'timestamps. Pass the actual T3 thread UUID as thread_id to bind tools explicitly; '
                'environment identity is only a convenience. Quiet mode blocks all speech output. Never '
                'infer physical motion or cleanup from command acknowledgements.')

LEGACY_TOOLS = ('cammy_status', 'cammy_device_status', 'cammy_open', 'cammy_frame', 'cammy_pan',
                'cammy_face', 'cammy_say', 'cammy_listen', 'cammy_stop')


def create_legacy_server(client=None):
    from cammy_agent_client import AgentClient
    from cammy_agent_core import AgentError
    client = client or AgentClient(endpoint=os.environ.get('CAMMY_AGENT_ENDPOINT', 'http://127.0.0.1:8791'),
                                   token_file=os.environ.get('CAMMY_AGENT_TOKEN_FILE'))
    server = MCPServer('Cammy (legacy)', version='0.1.0', instructions=INSTRUCTIONS)

    def fail(error, fallback):
        e = error if isinstance(error, AgentError) else AgentError(503, fallback)
        return failure(str(e), getattr(e, 'code', 503))

    def call(operation, body=None, session=None, thread_id=None):
        try:
            scoped = client.for_thread(thread_id) if thread_id else client
            return result(scoped.request(operation, body, scoped.session(session)
                                         if operation in ('stop', 'pan', 'face', 'say', 'listen') else None))
        except Exception as e:
            return fail(e, 'Cammy agent service unavailable')

    @server.tool()
    def cammy_status(thread_id: str | None = None) -> CallToolResult:
        """Read control/quiet/session state. Does not start sensing or take a lease."""
        return call('status', thread_id=thread_id)

    @server.tool()
    def cammy_device_status(thread_id: str | None = None) -> CallToolResult:
        """Read configured Cammy gateway identity/state/capabilities; no capture."""
        return call('device', thread_id=thread_id)

    @server.tool()
    def cammy_open(seconds: int = 30, sensing: bool = False, thread_id: str | None = None) -> CallToolResult:
        """Explicitly open a5..90second owned control session. sensing=True starts live video AND microphone; no TF recording. Another owner is refused."""
        return call('open', {'seconds': seconds, 'sensing': sensing}, thread_id=thread_id)

    @server.tool()
    def cammy_frame(session: str | None = None, thread_id: str | None = None) -> CallToolResult:
        """Return a fresh JPEG image to this agent from an already opened sensing session, with boot/session/time metadata. Never substitutes an old photo."""
        try:
            scoped = client.for_thread(thread_id) if thread_id else client
            raw, metadata = scoped.request('frame.jpg', session=scoped.session(session), image=True)
            return CallToolResult(content=[TextContent(type='text', text=json.dumps(metadata)),
                                           ImageContent(type='image', data=base64.b64encode(raw).decode(),
                                                        mimeType='image/jpeg')])
        except Exception as e:
            return fail(e, 'Fresh camera frame unavailable')

    @server.tool()
    def cammy_pan(direction: str, seconds: float = .3, session: str | None = None,
                  thread_id: str | None = None) -> CallToolResult:
        """Command a finite0.1..5second left/right stock pan hold, with independent device release. No absolute angle or measured movement is claimed."""
        return call('pan', {'direction': direction, 'seconds': seconds}, session, thread_id)

    @server.tool()
    def cammy_face(expression: str = 'neutral', action: str = 'show', session: str | None = None,
                   thread_id: str | None = None) -> CallToolResult:
        """Request native LCD mood/show/hide/replay/idle. Requires installed native service; host preview is not substituted for the real display."""
        return call('face', {'expression': expression, 'action': action}, session, thread_id)

    @server.tool()
    def cammy_say(text: str, session: str | None = None, thread_id: str | None = None) -> CallToolResult:
        """Generate one short local-model phrase and queue it on Cammy's own speaker. Refuses in quiet mode; never uses P15 as fallback."""
        return call('say', {'text': text}, session, thread_id)

    @server.tool()
    def cammy_listen(seconds: int = 3, session: str | None = None, thread_id: str | None = None) -> CallToolResult:
        """Collect1..8seconds of new live microphone PCM and transcribe locally. Requires an explicit sensing session; no cloud upload/TF recording."""
        return call('listen', {'seconds': seconds}, session, thread_id)

    @server.tool()
    def cammy_stop(session: str | None = None, thread_id: str | None = None) -> CallToolResult:
        """Cancel this actor's session/jobs and release control; never stops another actor. Acknowledgement is not proof of physical cleanup."""
        return call('stop', {}, session, thread_id)

    allowed = {'cammy_status': set(), 'cammy_device_status': set(), 'cammy_open': {'seconds', 'sensing'},
               'cammy_frame': {'session'}, 'cammy_pan': {'direction', 'seconds', 'session'},
               'cammy_face': {'expression', 'action', 'session'}, 'cammy_say': {'text', 'session'},
               'cammy_listen': {'seconds', 'session'}, 'cammy_stop': {'session'}}
    for keys in allowed.values():
        keys.add('thread_id')
    assert set(allowed) == set(LEGACY_TOOLS)
    server.middleware.append(strict_fields(allowed))
    return server
