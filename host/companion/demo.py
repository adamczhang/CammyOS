"""Finite, offline companion demo against the in-process simulator only.

There is deliberately no endpoint or token option: this command cannot reach a
gateway, the body or a native bridge. It uses a virtual clock, scripted fake
providers and prints JSON lines to stdout. Nothing is written to disk; no
microphone, speaker, camera or network is used.
SPDX-License-Identifier: MIT
"""
import argparse
import json
from pathlib import Path
import sys

from .body import SimulatorBody
from .contracts import AgentReply
from .fakes import EstimatedTextSpeech, ManualClock, ScriptedAgent, ScriptedTranscriber
from .service import CompanionConfig, CompanionService
from .engagement import EngagementConfig, EngagementController

HOST = str(Path(__file__).resolve().parent.parent)


def build(profile, clock):
    if HOST not in sys.path:
        sys.path.insert(0, HOST)
    from cammy_simulator import Simulator
    simulator = Simulator(profile, clock=clock)
    def reply(request):
        if 'hello cammy' in request.text:
            return AgentReply('[synthetic] That transcript came from a scripted fake, not your voice.')
        if 'long' in request.text:
            return AgentReply('[synthetic] This deliberately long reply exists to be interrupted. ' * 3)
        if 'new question' in request.text:
            return AgentReply('[synthetic] Interrupted; answering the newer request instead.', gaze_pan_degrees=-10)
        return AgentReply('[synthetic] I can look, show a face and speak text, all simulated.', gaze_pan_degrees=15)
    agent = ScriptedAgent(clock, reply, delay=1.0)
    transcriber = ScriptedTranscriber(clock, [None, '[synthetic transcript] hello cammy'], delay=0.3)
    service = CompanionService(SimulatorBody(simulator, 'cammy-companion-demo'), agent,
                               EstimatedTextSpeech(clock, chars_per_second=20), transcriber,
                               CompanionConfig(lease_ttl=10, renew_margin=4), clock)
    return simulator, service


def engagement_demo(profile, quiet):
    """Exercise the integrated policy with the same simulator and fake providers."""
    clock = ManualClock()
    simulator, service = build(profile, clock)
    controller = EngagementController(service, EngagementConfig(idle_seconds=3,
                                       max_session_seconds=20, max_hold_seconds=5))
    trace = []

    def record(event):
        state = controller.status()
        item = {'event': event, 'engagement': state['engagement'], 'reason': state['reason'],
                'listening_requested': state['listening_requested'],
                'captures': service.counters['captures'], 'synthetic': True}
        trace.append(item)
        if not quiet:
            print(json.dumps(item, sort_keys=True))

    def run(seconds):
        for _ in range(round(seconds / 0.25)):
            clock.advance(0.25)
            controller.tick()

    controller.start(); run(2); record('idle')
    controller.press(); run(1); record('held')
    controller.release(); run(10); record('followup-expired')
    controller.agent_wake(); run(0.5); record('agent-wake')
    controller.mute(); run(1); record('muted')
    wake = controller.agent_wake()
    controller.unmute(); run(1); record('unmuted-idle')
    controller.stop()
    print(json.dumps({'event': 'engagement-summary', 'synthetic': True, 'trace': trace,
                      'muted_wake_accepted': wake['accepted'], 'status': controller.status(),
                      'note': 'In-process simulator only; physical record button and duplex are unverified.'},
                     sort_keys=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline companion demo (in-process simulator only)')
    parser.add_argument('--profile', choices=('companion', 'stock'), default='companion',
                        help='simulator profile; stock reports body adapters unavailable')
    parser.add_argument('--fault', choices=('none', 'disconnect'), default='none',
                        help='inject a simulated disconnect during a delayed agent turn, then reconnect')
    parser.add_argument('--quiet', action='store_true', help='print only the final summary')
    parser.add_argument('--engagement', action='store_true',
                        help='exercise idle, hold/release, follow-up timeout, agent wake and mute offline')
    args = parser.parse_args(argv)
    if args.engagement:
        if args.fault != 'none':
            parser.error('--engagement has its own finite scenario; omit --fault')
        return engagement_demo(args.profile, args.quiet)
    clock = ManualClock()
    simulator, service = build(args.profile, clock)
    last = [None]

    def emit(kind, **data):
        if not args.quiet:
            print(json.dumps({'t': round(clock() - 1000, 2), 'event': kind, 'synthetic': True, **data}, sort_keys=True))

    def run(seconds, step=0.25):
        for _ in range(int(seconds / step)):
            clock.advance(step)
            status = service.tick()
            now = (status['run_state'], status['phase'])
            if now != last[0]:
                last[0] = now
                emit('phase', run_state=now[0], phase=now[1], face=(status['face'] or {}).get('commanded'))

    started = service.start()
    emit('start', started=started['started'], reason=started.get('reason'),
         capabilities={k: v['evidence'] for k, v in service.capabilities().items()})
    emit('input', **vars(service.submit_text('What can you do?')))
    run(6)
    emit('sensing', **vars(service.enable_sensing()))
    run(8)
    service.disable_sensing()
    emit('input', **vars(service.submit_text('Tell me something long.')))
    run(1.5)
    emit('input', note='barge-in while speaking', **vars(service.submit_text('Stop, new question.')))
    run(6)
    if args.fault == 'disconnect':
        service.submit_text('A question that will be cut off.')
        run(0.5)
        simulator.settings({'disconnected': True})
        run(2.5)   # the next health check (every 2 s) detects it
        emit('fault', halt_reason=service.status()['halt_reason'], body_stop=service.status()['body_stop'])
        simulator.settings({'disconnected': False})
        emit('reconnect', started=service.start()['started'])
        run(2)
    service.pause()
    emit('paused', face=(service.status()['face'] or {}).get('commanded'))
    service.resume()
    stopped = service.stop()
    final = service.status()
    summary = {'event': 'summary', 'synthetic': True, 'stopped': stopped, 'counters': final['counters'],
               'spoken': list(service.spoken), 'captions': list(service.captions),
               'physical_capabilities_verified': final['physical_capabilities_verified'],
               'note': 'Simulator and scripted fakes only: no audio was heard or played, no hardware moved.'}
    print(json.dumps(summary, sort_keys=True))
    return 0
