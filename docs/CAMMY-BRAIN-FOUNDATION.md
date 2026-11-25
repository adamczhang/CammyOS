# Cammy technical foundation

Cammy is a desk companion based on the AT-M105 camera with an Allwinner V853.
Its LCD shows its face, the gimbal aims its gaze, and its camera, microphone and
speaker provide media. Cammy Control Center (CCC) on the PC is the resident
control owner. It supervises the camera lease and the selected Brain profile.
Physical controls and stock camera operation remain camera-side.
In CammyOS 0.7.13 and later, the physical M button pauses or resumes the
CCC microphone and Look Back sampler. Earlier Look Back frames remain local.

CammyOS is TF-backed userspace. The stock bootloader, kernel, drivers and
original camera app are the baseline. A checked release has a bounded TF slot
and reviewed internal files for the stock launch hook, TF startup and native
selection. Wi-Fi profiles, fallback AP and portal credentials are provisioned
separately through authorized USB. A stale native network name or incompatible
Wi-Fi config can prevent media even when the candidate files verify. The early
quiet-start hook and setting are separate from the release transaction.

Keep running, installed and staged separate. The portal reports a running
version and boot ID. An authorized USB observation can identify the release
marker on the same boot. An installed receipt is a record of a transaction and
needs camera readback to describe current boot files. A local reviewed build,
a TF directory and a fully checked TF slot are different observations. A
successful install arms one manual boot. Hardware health, usable media,
resident authority and power-cut retry require separate verification or
authorization. The camera may power off on ADB reboot. CCC asks the operator
to power it off and turn it back on physically.

The adult operator can type these exact local commands into CCC Brain:

- `/os status` reports the running portal observation if available.
- `/os prepare ACTION CANDIDATE [RECEIPT]` reviews the local artifact.
- `/os request ACTION CANDIDATE [RECEIPT]` leaves intent for desktop review.

Actions are `first-device` for an exact factory-stock baseline, `install` for
checked replacement of a receipt-matched installation, `repair` for a checked
reapplication of the same release from stock or matching installed files, and
`rollback` for restoring checked before-images with stock selection disabled.
The last three need a receipt. Use the candidate and receipt identifiers listed
in desktop OS. Only the local operator can approve an exact action against its
validated artifact, USB transport, device, boot and readback state. The Brain
cannot approve writes, run installer commands from its provider sandbox, supply
paths or endpoints, change boot authority, provision secrets, or clear a guard.
Kids mode has no Brain firmware actions. Desktop maintenance uses separate
local authority and works with no portal report. Calendar authorization is
independent.

For a first device or off-network recovery, use desktop OS inspection with
the owner-authorized maintenance USB setup. Connect the verified AT-M105 by
USB, choose Charging if prompted and use a camera-formatted TF card. The
project maintenance ADB server must already be listening on loopback port
5038 and report one authorized USB transport. Stock
AT-M105 previously provided limited read-only inspection. A prepared
maintenance USB route worked on the owner's unit. TF-to-ROM FEL entry and
complete SPI readback were demonstrated. RAM handoffs did not establish a
working boot and raw flash restore is unverified. Stop if the documented
authorized shell, exact stock provenance or TF identity is unavailable.
See [investigation history](INVESTIGATION-README.md#4-joining-the-camera-and-obtaining-root).

## Desktop deployment

The CCC checkout needs this document, the OS panel and the `tools` lifecycle
modules. Before the first local approval, run
`python3 -m tools.cammy_lifecycle enroll` in an interactive project terminal.
Use a dedicated maintenance passphrase. It is stored as a salted verifier in
the private lifecycle directory. The OS tab can load a Brain request or
prepare a local one. The interactive CLI also supports `catalog`,
`status --offline` for a local-only inventory, `inspect-recovery --artifact
CANDIDATE` for read-only USB, `prepare ACTION CANDIDATE` and
`apply ACTION CANDIDATE` with `--receipt RECEIPT` where required.

Local artifacts and historical receipts are under `private/rc06`. New
transactions, checkpoints and before/after evidence are under
`private/rc06/lifecycle/jobs`. A blocked or interrupted job should be inspected
before retrying. The workflow stages whole releases and preserves prior
boot files and fault evidence. Restore Wi-Fi, fallback AP, portal credentials,
the native name and quiet-start policy separately using the documented owner
procedure. See [Home Wi-Fi](HOME-WIFI.md) for the existing network setup.
Successful offline or USB checks establish only the stages they actually tested.

## Checked update and recovery

1. Read [current status](STATUS.md) and verify the candidate on LAB.
   In CCC use **OS → Inspect recovery USB**. Match the serial, current
   boot, camera-formatted TF card, stock binary and installed receipt.
   If USB enumerates while shell reads time out, reconnect the cable
   with the camera powered and inspect again. A reconnect or boot change
   invalidates a prepared approval.
2. Check internal flash and TF space separately. The `/overlay` filesystem
   is 512 KiB and must fit the rollback before-images plus staged boot files.
   Cammy's BusyBox may lack `df`. Use a read-only `statfs("/overlay")` probe
   if needed. Keep the factory originals and current rollback. Verify older
   backups against LAB before clearing camera copies. Removing a TF slot
   frees card space only.
3. Choose **Install / update** with the candidate and matching receipt,
   then **Prepare action** and approve the exact review. The checked
   installer validates every on-card file, backs up the boot files and sets
   the selector last. If a write fails, leave the camera powered on and
   inspect the transaction journal and selector before retrying. A disabled
   selector keeps the next boot on stock. For a terminal install, resume
   the same candidate and transaction after correcting the cause.
4. If a startup guard or fault blocks the action, read the selector decision,
   boot ID, guard and fault together. A used one-shot can produce
   `post-attempt-boot` followed by `native-exe` on stock. A failed candidate
   can also leave records. Get the operator's approval for that specific
   recovery, archive and verify the evidence on LAB, and use the checked
   installer to archive the guard. Leave resident and retry authority to
   their separate approval workflows.
5. After installation, use the physical power button to restart and select
   CammyOS once. ADB reboot powered this camera off in prior trials. Confirm
   the new boot ID, selected release, Wi-Fi and CCC preview. Loading can
   clear before the background health window ends. Keep the camera on until
   the release health record matches this boot and the startup guard clears.
   A second camera reboot needs a new arm or separately approved resident
   authority. Restarting the PC alone leaves the camera's boot intact.
