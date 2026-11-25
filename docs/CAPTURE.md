# Taking pictures and videos with Cammy (2026-11-16)

Adam's flow: he asks Cammy to take a picture (or a video); Cammy's screen switches to the
stock camera's live view; he frames the shot and presses the **red record button**
(video: press to start, press again to stop); the result goes to Cammy and the face
comes back.

## How it works (`host/cammy_capture.py`, tools in `host/cammy_gemini_live.py`)

1. Gemini calls `take_picture` or `take_video`. The tool answers at once ("Live view is
   on. Tell Adam to ... press the red record button"), so Cammy can say it; the capture
   runs in the background.
2. The stock camera API (port 8082) is read: mode and recording state; the list of
   `/mnt/extsd/DCIM/100MEDIA/` is remembered. If needed the mode is switched (4 photo,
   0 video) and confirmed.
3. Cammy's face is hidden through the portal (the reversible `hide`), so the stock live
   view shows. The record button is never grabbed: it does what it always does.
4. Every 0.5 s the listing (and, for video, the recording state) is read. The first new
   finished `.jpg` (photo) or `.mp4` (video, after recording stops) is the result.
   Timeouts: 2 minutes to press; a recording is stopped at 10 minutes.
5. The face comes back and the camera's original mode is restored, then the file is
   copied to the PC as `private/captures/<date>/<name>` (photos lose the M105's 16-byte
   footer).
6. Cammy's captures do not stay in the stock media viewer (Adam, 2026-11-16). Once the PC
   copy reads back (the photo decodes, or frames come out of the clip), that one file is
   deleted with the stock app's own delete: `POST /api/setdeviceinfo?custom=1&cmd=4003&par=
   /mnt/extsd/DCIM/100MEDIA/<name>` (`IOTYPE_CDR_DELET_ONE_FILE`, found in the stock binary).
   It removes the file, its `DCIM/.thumb` sidecars and its row in the viewer's database
   (`/tmp/.data/sqlite/sunxi.db`, rebuilt from DCIM at each boot). Its reply is always
   Value 0, so the listing is re-read to confirm. If the copy is bad or the delete does not
   take, the file stays on the card. Anything Adam records himself, in Normal mode or
   CammyOS mode, is never touched: only the file that appeared during this capture is
   deleted. Never send 1030 (format card) or 1031 (factory reset), which sit in the same
   command set.
7. When Cammy is quiet, Gemini gets a `[Camera]` message with the photo (1024 px) or four
   frames from the clip (768 px) and says what it shows. A failed or timed-out capture is
   reported the same way.

Checked on the PC: the flow against a scripted camera (`tests/test_cammy_capture.py`), the
tool path (`tests/test_gemini_live.py`), and that gemini-3.8-live describes an image sent
in a normal message turn (2026-11-16).

## Open until tried on Cammy

- Whether stock switches mode and records while our RTSP preview runs (it carries Cammy's
  mic, so the loop keeps it on). If not, the preview must pause during a capture.
- What the screen shows under the hidden face in CammyOS mode: the live view, or the USB
  Charging/Webcam prompt when USB is connected.
- That one press in photo mode takes one picture, and the button-made file names.
- That cmd 4003 deletes on hardware (decoded from the binary, not yet sent to Cammy).
- The recording preset: at 4K a 30 s clip is ~125 MB to copy over Wi-Fi.
