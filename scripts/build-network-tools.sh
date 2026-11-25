#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
findmnt -n "${CAMMY_WORK_MOUNT:-/}" >/dev/null
cd -- "$(dirname -- "$0")/.."
printf '%s  %s\n' 34f9ea6ff8636f2c9241153b9114eefa9e65674a45318ae1ef95bb5f31c53bb2 downloads/busybox-1.38.0.tar.bz2 | sha256sum -c -
if [[ ! -d build/busybox-dhcp-src ]]; then
 mkdir build/busybox-dhcp-src
 tar -xf downloads/busybox-1.38.0.tar.bz2 --strip-components=1 -C build/busybox-dhcp-src
fi
compiler="$PWD/build/toolchain/bin/arm-buildroot-linux-musleabihf-"
make -C build/busybox-dhcp-src allnoconfig >/dev/null
python3 - <<'PY'
from pathlib import Path
r=Path.cwd();p=r/'build/busybox-dhcp-src/.config';s=p.read_text()
for k in ['BUSYBOX','SHOW_USAGE','STATIC','LFS','UDHCPC','IFCONFIG','FEATURE_IFCONFIG_STATUS','ROUTE','HOSTNAME','MKDIR','CHMOD','CP','RM','SLEEP','KILL','SHA256SUM','FEATURE_MD5_SHA1_SUM_CHECK']:
 name='CONFIG_'+k;s=s.replace('# '+name+' is not set',name+'=y')
s=s.replace('CONFIG_EXTRA_LDFLAGS=""','CONFIG_EXTRA_LDFLAGS="-L'+str(r/'build/arm')+'"')
s=s.replace('CONFIG_EXTRA_LDLIBS=""','CONFIG_EXTRA_LDLIBS="cammycompat"');p.write_text(s)
PY
python3 - "$compiler" <<'PY'
import subprocess,sys
subprocess.run(['make','-C','build/busybox-dhcp-src','CROSS_COMPILE='+sys.argv[1],'oldconfig'],input='\n'*10000,text=True,stdout=subprocess.DEVNULL,check=True)
PY
make -C build/busybox-dhcp-src CROSS_COMPILE="$compiler" -j8 >/dev/null
cp build/busybox-dhcp-src/busybox build/arm/busybox-dhcp
file build/arm/busybox-dhcp
