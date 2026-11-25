"""CammyOS stage-5 acceptance evidence validator (WP5). Local only, no hardware backend.

It reads supplied JSON acceptance reports and decides whether the evidence in them
is complete, consistent and bound to the right identities and boots. It never
talks to a camera, card, device, process or network, and it never closes a gate:
its best verdict, HARDWARE_EVIDENCE_COMPLETE, still needs manager review.

Modules:
    spec      stages, profiles, identities, thresholds (machine-readable spec)
    validate  the validator: per-stage status and overall verdict
    fixtures  FIXTURE report builder (always fixture=true -> LOCAL_ONLY at best)
    cli       ``python3 tools/native_acceptance {spec,validate,template}``
"""
