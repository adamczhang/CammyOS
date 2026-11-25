"""Generate only original ALSA profile data; vendor files stay on the camera."""
IPC_KEY = 0x434D5900
STOCK_ASOUND = '1e4399abc3506fcc71de5fcdc96eb5ae24dc825a1b605a2f2a4f01550190edf9'
STOCK_GLOBAL = 'b2776093cbe6c4560de86f08166ed97761e5324861394df72c2b0c4fde732d31'


def routing():
    return '''
pcm.!hw {
 @args [ CARD DEV SUBDEV ]
 @args.CARD { type string default "0" }
 @args.DEV { type integer default 0 }
 @args.SUBDEV { type integer default -1 }
 type asym
 playback.pcm { type hw card $CARD device $DEV subdevice $SUBDEV }
 capture.pcm {
  @func concat
  strings [ "cammy_capture_" $CARD "_" $DEV "_" $SUBDEV ]
 }
}
pcm."cammy_capture_0_0_-1" "cammy_shared"
pcm."cammy_capture_0_0_0" "cammy_shared"
'''


def fragment():
    return ('''# Cammy experimental robot-mode capture; unknown capture cards fail closed.
pcm.cammy_shared {
 type dsnoop
 ipc_key %d
 ipc_key_add_uid false
 ipc_perm 0600
 slave {
  pcm { type hw card 0 device 0 }
  format S16_LE
  rate 16000
  channels 1
  period_size 960
  buffer_size 7680
 }
 bindings.0 0
}
''' % IPC_KEY) + routing()
