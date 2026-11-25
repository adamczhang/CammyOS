#!/usr/bin/env python3
"""Stdlib client for the CammyOS status bridge, protocol 1.0 (WP2).

An independent implementation of docs/native-bridge/PROTOCOL.md used by host
fixtures and for manual local checks. It talks only to a local Unix socket.
SPDX-License-Identifier: MIT
"""
import argparse
import json
import socket
import struct
import sys
import time
import uuid

PROTO_MAJOR, PROTO_MINOR = 1, 0
REQUEST_SIZE = 64
MAX_FRAME = 256
REQUEST_MAGIC, RESPONSE_MAGIC = b'CBRQ', b'CBRS'
# magic, major, minor, header_size, command, request_id, flags, reserved,
# expect_instance, boot_id, deadline_mono_ms, owner_generation
REQUEST = struct.Struct('<4sHHHHIIIQ16sQQ')
# magic, major, minor, header_size, command, request_id, status, backend_kind,
# payload_size, instance, boot_id, mono_now_ms, owner_generation
RESPONSE_HEADER = struct.Struct('<4sHHHHIHHIQ16sQQ')
ERROR_PAYLOAD = struct.Struct('<HHHHII')
HELLO_PAYLOAD = struct.Struct('<HHHHIIIIII16s')
STATUS_PAYLOAD = struct.Struct('<HHHHQQ10I')
CAPS_HEADER = struct.Struct('<HHI')
CAP_ENTRY = struct.Struct('<HBBB3s')
assert REQUEST.size == 64 and RESPONSE_HEADER.size == 64
assert HELLO_PAYLOAD.size == 48 and STATUS_PAYLOAD.size == 64 and CAP_ENTRY.size == 8

HELLO, STATUS, CAPABILITIES = 1, 2, 3
COMMANDS = {'hello': HELLO, 'status': STATUS, 'capabilities': CAPABILITIES}
STATUS_NAMES = {0: 'ok', 1: 'bad_magic', 2: 'bad_length', 3: 'too_large', 4: 'unsupported_version',
                5: 'unknown_field', 6: 'bad_request_id', 7: 'unknown_command', 8: 'mutation_disabled',
                9: 'owner_unsupported', 10: 'stale_instance', 11: 'boot_mismatch', 12: 'clock_domain',
                13: 'expired', 14: 'bad_deadline', 15: 'busy', 16: 'peer_denied', 17: 'internal',
                18: 'bad_ancillary'}
BACKEND_KINDS = {0: 'none', 1: 'fake', 2: 'native'}
BACKEND_STATES = {0: 'unavailable', 1: 'ready', 2: 'error'}
BACKEND_REASONS = {0: 'none', 1: 'fake_backend', 2: 'native_table_absent', 3: 'native_table_rejected',
                   4: 'init_failed', 5: 'query_failed', 6: 'native_exiting', 7: 'native_identity_mismatch'}
CAP_IDS = {1: 'status_ipc', 2: 'video_stream', 3: 'audio_capture', 4: 'audio_playback', 5: 'gimbal',
           6: 'display', 7: 'lifecycle_handoff'}
CAP_STATES = {0: 'unavailable', 1: 'available', 2: 'simulated'}
CAP_REASONS = {0: 'none', 1: 'fake_backend', 2: 'native_table_absent', 3: 'not_in_protocol_v1',
               4: 'backend_error', 5: 'native_table_rejected'}
STATUS_COUNTERS = ('active_clients', 'connections_accepted', 'rejected_busy', 'rejected_peer',
                   'requests_ok', 'requests_rejected', 'framing_errors', 'idle_disconnects',
                   'slow_disconnects', 'peer_disconnects')


class ProtocolError(Exception):
    """The bridge sent something that is not a valid protocol-1 response."""


def read_boot_id(path='/proc/sys/kernel/random/boot_id'):
    """Boot identity as 16 bytes, or None if unavailable."""
    try:
        with open(path, encoding='ascii') as f:
            return uuid.UUID(f.read(64).strip()).bytes
    except (OSError, ValueError):
        return None


def monotonic_ms():
    return int(time.monotonic() * 1000)


def encode_request(command, request_id, *, major=PROTO_MAJOR, minor=PROTO_MINOR, header_size=REQUEST_SIZE,
                   flags=0, reserved=0, expect_instance=0, boot_id=b'\0' * 16, deadline_mono_ms=0,
                   owner_generation=0, magic=REQUEST_MAGIC):
    return REQUEST.pack(magic, major, minor, header_size, command, request_id, flags, reserved,
                        expect_instance, boot_id, deadline_mono_ms, owner_generation)


def decode_response(frame):
    if len(frame) < RESPONSE_HEADER.size or len(frame) > MAX_FRAME:
        raise ProtocolError(f'bad response length {len(frame)}')
    (magic, major, minor, header_size, command, request_id, status, kind, payload_size, instance,
     boot_id, mono_now, owner) = RESPONSE_HEADER.unpack_from(frame)
    if magic != RESPONSE_MAGIC or major != PROTO_MAJOR or header_size != 64:
        raise ProtocolError('bad response header')
    payload = frame[64:]
    if payload_size != len(payload):
        raise ProtocolError('payload size mismatch')
    out = {'major': major, 'minor': minor, 'command': command, 'request_id': request_id,
           'status': status, 'status_name': STATUS_NAMES.get(status, f'unknown:{status}'),
           'backend_kind': BACKEND_KINDS.get(kind, f'unknown:{kind}'), 'instance': instance,
           'boot_id': boot_id, 'mono_now_ms': mono_now, 'owner_generation': owner}
    if status != 0:
        if len(payload) != ERROR_PAYLOAD.size:
            raise ProtocolError('bad error payload')
        smaj, smin, field, disposition, req_size, remaining = ERROR_PAYLOAD.unpack(payload)
        out['error'] = {'supported_major': smaj, 'supported_minor': smin,
                        'field_offset': None if field == 0xFFFF else field,
                        'closing': disposition == 1, 'request_size': req_size, 'errors_remaining': remaining}
    elif command == HELLO:
        v = HELLO_PAYLOAD.unpack(payload)
        out['hello'] = {'proto_major': v[0], 'proto_minor_max': v[1], 'max_clients': v[2], 'max_burst': v[3],
                        'request_size': v[4], 'max_frame': v[5], 'idle_timeout_ms': v[6],
                        'max_deadline_horizon_ms': v[7], 'command_mask': v[8], 'error_budget': v[9],
                        'bridge_version': v[10].rstrip(b'\0').decode('ascii')}
    elif command == STATUS:
        v = STATUS_PAYLOAD.unpack(payload)
        out['status_payload'] = {
            'backend_kind': BACKEND_KINDS.get(v[0], f'unknown:{v[0]}'),
            'backend_state': BACKEND_STATES.get(v[1], f'unknown:{v[1]}'),
            'backend_reason': BACKEND_REASONS.get(v[2], f'unknown:{v[2]}'),
            'flags': v[3], 'boot_id_available': bool(v[3] & 1),
            'hardware_actions_enabled': bool(v[3] & 2), 'owner_present': bool(v[3] & 4),
            'start_mono_ms': v[4], 'uptime_ms': v[5], **dict(zip(STATUS_COUNTERS, v[6:]))}
    elif command == CAPABILITIES:
        count, entry_size, _ = CAPS_HEADER.unpack_from(payload)
        if entry_size != CAP_ENTRY.size or len(payload) != 8 + count * entry_size:
            raise ProtocolError('bad capabilities payload')
        caps = []
        for i in range(count):
            cid, state, reason, flags, _ = CAP_ENTRY.unpack_from(payload, 8 + i * entry_size)
            caps.append({'id': CAP_IDS.get(cid, f'unknown:{cid}'), 'state': CAP_STATES.get(state, f'unknown:{state}'),
                         'reason': CAP_REASONS.get(reason, f'unknown:{reason}'), 'physical': bool(flags & 1)})
        out['capabilities'] = caps
    return out


class BridgeClient:
    """One SEQPACKET connection; request IDs increase automatically."""

    def __init__(self, path, timeout=3.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect(str(path))
        except OSError:
            self.sock.close()
            raise
        self.next_id = 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.sock.close()

    def send_raw(self, data):
        self.sock.send(data)

    def recv_raw(self):
        return self.sock.recv(MAX_FRAME + 1)

    def call(self, command, request_id=None, **fields):
        if request_id is None:
            request_id = self.next_id
        self.next_id = max(self.next_id, request_id + 1)
        self.send_raw(encode_request(command, request_id, **fields))
        frame = self.recv_raw()
        if not frame:
            raise ConnectionError('bridge closed the connection')
        return decode_response(frame)


def _jsonable(v):
    if isinstance(v, bytes):
        return v.hex()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--socket', required=True)
    parser.add_argument('command', choices=sorted(COMMANDS))
    args = parser.parse_args(argv)
    with BridgeClient(args.socket) as client:
        print(json.dumps(_jsonable(client.call(COMMANDS[args.command])), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
