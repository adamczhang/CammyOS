"""Explicit Brain commands for CammyOS maintenance intent."""

import asyncio
from pathlib import Path
import sys

# The user service starts in host/. Import the fixed project tools by path.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cammy_lifecycle import Blocked, Lifecycle, Request, public_error


def parse(text):
    words = text.strip().split(maxsplit=5)
    if not words or words[0] != '/os':
        return None
    if words == ['/os', 'status']:
        return 'status', None
    if len(words) in {4, 5} and words[1] in {'prepare', 'request'}:
        return words[1], Request(*words[2:])
    raise Blocked('Use /os status or /os prepare|request ACTION CANDIDATE [RECEIPT].')


class OsRouter:
    def __init__(self, c3, lifecycle=None):
        self.c3 = c3
        self.lifecycle = lifecycle if lifecycle is not None else Lifecycle()

    async def route(self, text, *, speak=False):
        if text.strip().split(maxsplit=1)[:1] != ['/os']:
            return None
        response = {'capability': 'cammy-os', 'status': 'blocked'}
        if speak or not self.c3.family.allows('cammy-os') or not self.c3.selected_profile():
            return {**response, 'message': 'CammyOS commands require the selected Adult Brain text channel.'}
        gate = self.c3._calendar_profile_key()  # selected profile and its selection generation only
        generation = self.c3.family.generation

        def allowed():
            return (self.c3.family.allows('cammy-os') and self.c3.family.generation == generation
                    and self.c3._calendar_profile_key() == gate)

        try:
            command, request = parse(text)
            if command == 'status':
                observed = await self.c3.read_device_state()
                status = await asyncio.to_thread(self.lifecycle.status, portal=observed)
                report = status['running']
                if report:
                    message = (f"Portal reports CammyOS {report['version']} on boot {report['boot_id']}. "
                               'Use desktop OS inspection for running release, installed files and TF status.')
                else:
                    message = ('The running camera is unconfirmed. Use desktop OS → Inspect recovery '
                               'with authorized USB for stock or off-network inspection.')
            elif command == 'prepare':
                review = await asyncio.to_thread(self.lifecycle.review_request, request)
                message = (f"Reviewed local {review['version']} artifact {review['release']}. "
                           'USB preflight and exact local approval are in desktop OS.')
            else:
                review = await asyncio.to_thread(self.lifecycle.queue, request, allowed)
                message = (f"Requested {request.action} for {review['release']}. "
                           'The operator can load it in desktop OS for USB preflight and approval.')
            if not allowed():
                raise Blocked('The active Adult Brain changed. Start a new request.')
        except (Blocked, OSError, RuntimeError, ValueError, TypeError, KeyError, SystemExit) as exc:
            return {**response, 'message': public_error(exc)}
        return {**response, 'status': 'local', 'message': message}


async def compose(os_router, calendar, text, *, speak=False):
    result = await os_router.route(text, speak=speak)
    if result is None and calendar is not None:
        return await calendar.route(text, speak=speak)
    return result
