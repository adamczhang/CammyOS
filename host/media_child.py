"""Linux exec shim: decoder dies with its exact parent, including startup races."""
import ctypes
import os
import signal
import sys


def main():
    parent = int(sys.argv[1])
    if parent <= 1 or os.getppid() != parent:
        print('media-child parent identity mismatch',file=sys.stderr)
        return 125
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        print('media-child parent guard unavailable',file=sys.stderr)
        return 125
    if os.getppid() != parent:
        print('media-child parent changed during setup',file=sys.stderr)
        return 125
    os.execv(sys.argv[2], sys.argv[2:])


if __name__ == '__main__':
    sys.exit(main())
