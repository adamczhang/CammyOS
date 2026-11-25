# Cammy Control Center on Windows (WSL2)

Status for v1.0-alpha1: **written for testers, not yet run on a Windows PC.** The
Linux parts it relies on are the ones Cammy runs on today; the Windows steps around
them (WSL setup, mirrored networking, the logon task) are new. Please report what
happens at each step.

Cammy Control Center (C3, or CCC) is the PC program that connects to Cammy, runs the
voice conversation and shows the CCC window. It is a Linux program. On Windows it runs
unchanged inside WSL2, the Linux that ships with Windows. Cammy's microphone, speaker
and camera are on the camera itself and reach the PC over Wi-Fi, so Windows needs no
audio or USB setup.

This guide covers running C3 on Windows for a camera that **already runs CammyOS**.
Installing CammyOS onto a camera is not possible from Windows yet; see
[the release notes](RELEASE-1.0-ALPHA1.md#who-can-install-it).

## What you need

- Windows 11, version 22H2 or later (Settings > System > About shows the version).
  Mirrored networking and the Linux window support need it.
- Virtualization turned on in the PC's firmware (most PCs have it on; WSL tells you if not).
- About 6 GB of free disk space.
- An administrator account for step 1.
- A Gemini API key (from Google AI Studio). C3's voice uses Gemini Live.
- Cammy running CammyOS, on the same Wi-Fi network as the PC, and the camera's portal
  token from its installation (a text file; the person who installed CammyOS has it).

## 1. Prepare Windows (once, as administrator)

1. Download `Setup-CammyWSL.ps1` from the release's `tools/windows` folder.
2. Open **Terminal (Admin)** or **PowerShell (Admin)**, go to the folder you saved it in,
   and run:

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\Setup-CammyWSL.ps1
   ```

It checks your Windows version, installs or updates WSL and Ubuntu 24.04, turns on
mirrored networking and adds a logon task named **CammyOS WSL** that keeps Ubuntu
running in the background, so Cammy stays connected without an open window.

The script stops and tells you when something needs you:

- **"WSL was installed. Restart Windows"**: restart, then run the script again.
- **"Finish creating the Linux user"**: Ubuntu opened a window asking for a new user
  name and password. Choose them (they are only for Ubuntu), then run the script again.

Mirrored networking matters: it gives Ubuntu the PC's own home-network address. Without
it, C3 cannot find the camera by itself (see Troubleshooting).

## 2. Get CammyOS inside Ubuntu

Open **Ubuntu 24.04** from the Start menu and run:

```bash
git clone https://github.com/adamczhang/CammyOS.git ~/CammyOS
cd ~/CammyOS
git checkout v1.0-alpha1
```

Keep the checkout inside Ubuntu (`~/CammyOS`), not under `/mnt/c`: C3 uses Linux file
permissions to protect its tokens, and Windows folders do not have them.

## 3. Run the setup script

```bash
./tools/windows/setup-c3-wsl.sh
```

It asks for your Ubuntu password when it installs packages. It:

- turns on systemd in Ubuntu if it is off (then asks you to run `wsl --shutdown` in
  PowerShell and run the script again),
- installs ffmpeg, the GTK libraries for the CCC window and build tools,
- makes the Python 3.12 environment `.venv-live` from `host/requirements-c3.txt`,
- builds the echo canceller (optional; without it, C3 runs in half duplex only),
- downloads the speech model that listens for "Cammy" (78 MB, checked by SHA-256) to
  `~/cammy-models`,
- installs C3 as a background service (enabled, not started) and keeps it running
  without a terminal (`loginctl enable-linger`),
- adds **Cammy Control Center** to the Windows Start menu,
- looks for Cammy on your network.

`./tools/windows/setup-c3-wsl.sh --check` reports what is missing without changing
anything. Run the script again after fixing any **TODO** line.

## 4. Add your credentials

These stay on your PC, in the `private/` folder, which git never uploads.

```bash
cd ~/CammyOS
mkdir -p private/credentials private/host
chmod 700 private private/credentials private/host

# Gemini API key: paste it, press Enter, then Ctrl+D
cat > private/credentials/gemini.key
chmod 600 private/credentials/gemini.key

# The camera's portal token: copy the token file you were given to private/host/portal.token
chmod 600 private/host/portal.token

# Camera settings
cat > private/host/config.json <<EOF
{
  "name": "CammyOS",
  "token_file": "$HOME/CammyOS/private/host/portal.token",
  "status_file": "$HOME/CammyOS/private/host/status.json",
  "port": 8790,
  "native_photo": true,
  "endpoints": ["http://192.168.10.1:8787"]
}
EOF
```

You do not need to know the camera's address: C3 searches the home network for it and
remembers where it found it (`private/c3/portal-address`). The address in `endpoints`
is the camera's own fallback Wi-Fi, used only when the camera cannot join your network.

Then run `./tools/windows/setup-c3-wsl.sh` once more; it should end with **Ready**.

## 5. Start C3 and open the window

```bash
systemctl --user start cammy-c3
```

Open **Cammy Control Center** from the Start menu (it may sit in an "Ubuntu" folder).
The window shows whether Cammy is connected. Its **Start** and **Stop** button at the
top right starts and stops C3; closing the window does not disconnect Cammy.

From then on C3 starts by itself when Windows signs you in, through the logon task.

## Daily use

| To | Do |
| --- | --- |
| Talk to Cammy | Say "Cammy" and talk. Close by, a clear voice works without the name. |
| See what Cammy is doing | Open Cammy Control Center |
| Disconnect Cammy | **Stop** in the window, or `systemctl --user stop cammy-c3` in Ubuntu |
| Reconnect | **Start** in the window, or `systemctl --user start cammy-c3` |
| Keep C3 from starting at sign-in | In Ubuntu: `systemctl --user disable cammy-c3` |
| Read C3's log | `journalctl --user -u cammy-c3 -n 50` |

Only one PC can hold Cammy's connection at a time. Stop C3 on one PC before starting it
on another.

## Troubleshooting

**C3 cannot find Cammy.** Check the camera is on, shows its face (not the stock camera
screen) and is on the same network. In PowerShell, `Get-Content $env:USERPROFILE\.wslconfig`
must show `networkingMode=mirrored` under `[wsl2]`; after changing it, run
`wsl --shutdown`. If you know the camera's address, you can name it: edit the service
(`systemctl --user edit cammy-c3`) and add `--portal http://ADDRESS:8787` to C3's
command, or run it by hand to test:
`cd ~/CammyOS/host && ../.venv-live/bin/python -m c3 --portal http://ADDRESS:8787`.
A DHCP reservation for the camera in your router keeps its address fixed.

**The window does not open.** Run `wsl --update` in PowerShell, then `wsl --shutdown`,
and try again. From Ubuntu, `python3 ~/CammyOS/host/c3/gui.py` shows any error.

**C3 stops when no Ubuntu window is open.** Check the logon task:
`Get-ScheduledTask -TaskName 'CammyOS WSL'` in PowerShell, and in Ubuntu
`loginctl show-user $USER -p Linger` should say `yes`.

**The voice never answers.** Check the key file exists and C3's log for Gemini errors
(`journalctl --user -u cammy-c3 | grep -i gemini | tail`).

**Windows Firewall prompts.** C3 only connects out to the camera and to Gemini, and its
own API listens on 127.0.0.1, so no inbound rule is needed.

## Removing it

In Ubuntu: `systemctl --user disable --now cammy-c3`. In PowerShell (admin):
`Unregister-ScheduledTask -TaskName 'CammyOS WSL'`. Removing Ubuntu itself
(`wsl --unregister Ubuntu-24.04`) deletes everything inside it, including
`~/CammyOS/private`.

## Dual boot: sharing a Linux project disk

For a PC that also boots Linux, with the CammyOS checkout on an ext4 disk of its own
(for example a whole second NVMe drive), Windows can hand that disk to WSL
so both systems use the same checkout, credentials and history. This needs the disk to
have no Windows drive letter, and only one system may use it at a time, which dual
booting guarantees.

```powershell
.\Setup-CammyWSL.ps1 -LinuxDiskModel NM790
```

The logon task then runs `wsl --mount` at every sign-in (as administrator, which
`wsl --mount` requires). In Ubuntu, link the disk where the Linux boot mounts it and use
a separate Python environment, since the Linux boot's `.venv-live` points at that
system's Python:

```bash
cd /mnt/wsl/PHYSICALDRIVE<N>p1/Projects/Cammy-OS
./tools/windows/setup-c3-wsl.sh --lab-from /mnt/wsl/PHYSICALDRIVE<N>p1 \
    --serve-from /mnt/d --venv .venv-wsl --model-dir /mnt/d/models/hf/ggerganov/whisper.cpp
```

Use the disk number and the SERVE drive letter the scripts report. The links are made
again at every WSL start (`/etc/tmpfiles.d/cammy-dualboot.conf`). The shared-memory
vault mirror stays off under WSL, because it requires SERVE to be a real mount.
Shut Windows down normally rather than forcing power off, so the ext4 disk is unmounted
cleanly.
