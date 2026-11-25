"""Original process-private ALSA playback data. Never changes global capture.

Fixed observed M105 native dmix identity/format; using it on hardware requires
fresh matching owner/PCM/mixer evidence. No raw exclusive output route is offered.
"""
FORMAT='cammy-private-speaker/1'
PCM='CammyPlayback'
IPC_KEY=1111
RATE=16000
CHANNELS=1
PERIOD=960
BUFFER=7680

def profile():
    return '''# Cammy process-private speaker profile; no global hooks or capture definitions.
timer.hw {
 @args [ CLASS SCLASS CARD DEV SUBDEV ]
 @args.CLASS { type integer default 2 }
 @args.SCLASS { type integer default 0 }
 @args.CARD { type integer default 0 }
 @args.DEV { type integer default 0 }
 @args.SUBDEV { type integer default 0 }
 type hw
 class $CLASS
 sclass $SCLASS
 card $CARD
 device $DEV
 subdevice $SUBDEV
}
ctl.hw {
 @args [ CARD ]
 @args.CARD { type string default "0" }
 type hw
 card $CARD
}
pcm.CammyPlayback {
 type plug
 slave.pcm {
  type softvol
  slave.pcm {
   type hooks
   slave.pcm {
    type dmix
    ipc_key 1111
    ipc_key_add_uid false
    ipc_perm 0666
    slave {
     pcm { type hw card 0 device 0 subdevice -1 }
     format S16_LE
     rate 16000
     channels 1
     period_size 960
     periods 8
    }
   }
   hooks.0 {
    type ctl_elems
    hook_args [
     { name "SPK Switch" preserve false optional false value 1 }
    ]
   }
  }
  control { name "Soft Volume Master" card 0 count 1 }
  min_dB -26.0
  max_dB 25.0
  resolution 256
 }
 rate_converter "linear"
}
'''
