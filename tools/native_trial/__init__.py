"""CammyOS native-app activation tooling (WP4): local, fail-closed, fake-tested.

Nothing in this package installs a boot hook, mounts a card, stops or starts a
native process, or touches a camera. Real native stop/exec/install backends are
deliberately absent: :mod:`native_trial.handoff` only accepts an injected
backend, and the default backend raises :class:`BackendUnavailable`.

Modules:
    records   strict key=value records shared with the shell selector proposal
    selector  Python reference model of native/activation/S00cammy-select.proposal
    faketree  fake root tree, fake mount/umount/sync/cp and power-loss reboot
    bundle    host-side STRUCTURAL classification, bundle/ack/allowlist staging
    review    SHA256-bound review/ABI evidence record (required beyond structure)
    handoff   gated late-handoff state machine and selected-boot verification
    policy    activation classes, hardware gates and the activation decision

WP5 review (2026-11-14): activation is ineligible; the selector proposal ships
SELECT_GATE=blocked and proposal-patch never emits a bundle-backed allowlist.
"""

__all__ = ['BackendUnavailable']


class BackendUnavailable(RuntimeError):
    """Raised by every real native/install backend entry point. Always."""
