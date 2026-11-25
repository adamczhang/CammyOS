#!/usr/bin/env bash
set -euo pipefail
findmnt -n "${CAMMY_WORK_MOUNT:-/}" >/dev/null
cd -- "$(dirname -- "$0")/.."
archive=downloads/busybox-1.38.0.tar.bz2
if [[ ! -f "$archive" ]]; then curl -fL https://busybox.net/downloads/busybox-1.38.0.tar.bz2 -o "$archive"; fi
printf '%s  %s\n' 34f9ea6ff8636f2c9241153b9114eefa9e65674a45318ae1ef95bb5f31c53bb2 "$archive" | sha256sum -c -
if [[ ! -d build/busybox-1.38.0 ]]; then tar -xf "$archive" -C build; fi
compiler="$PWD/build/toolchain/bin/arm-buildroot-linux-musleabihf-"
"${compiler}gcc" -Os -Wall -Wextra -c src/compat/arm_legacy_stat.c -o build/arm/arm_legacy_stat.o
"${compiler}ar" rcs build/arm/libcammycompat.a build/arm/arm_legacy_stat.o
make -C build/busybox-1.38.0 clean >/dev/null
make -C build/busybox-1.38.0 CROSS_COMPILE="$compiler" allnoconfig >/dev/null
python3 - <<'PY'
from pathlib import Path
p=Path('build/busybox-1.38.0/.config');s=p.read_text()
for key in ['BUSYBOX','SHOW_USAGE','STATIC','LFS','TAR','FEATURE_SEAMLESS_GZ','GUNZIP','ZCAT','FEATURE_GZIP_DECOMPRESS','CHROOT','CAT','MKDIR','CHMOD','PRINTF','CP','LS','RM','SHA256SUM']:
 name='CONFIG_'+key;s=s.replace('# '+name+' is not set',name+'=y')
s=s.replace('CONFIG_EXTRA_LDFLAGS=""','CONFIG_EXTRA_LDFLAGS="-L'+str(Path('build/arm').resolve())+'"')
s=s.replace('CONFIG_EXTRA_LDLIBS=""','CONFIG_EXTRA_LDLIBS="cammycompat"')
p.write_text(s)
PY
python3 - "$compiler" <<'PYCONFIG'
import subprocess,sys
subprocess.run(['make','-C','build/busybox-1.38.0','CROSS_COMPILE='+sys.argv[1],'oldconfig'],input='\n'*10000,text=True,check=True,stdout=subprocess.DEVNULL)
PYCONFIG
make -C build/busybox-1.38.0 CROSS_COMPILE="$compiler" -j8 >/dev/null
cp build/busybox-1.38.0/busybox build/arm/busybox
file build/arm/busybox
