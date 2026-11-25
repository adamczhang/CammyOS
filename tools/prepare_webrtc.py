#!/usr/bin/env python3
"""Pinned Ubuntu/Arch WebRTC runtime extracted only under this project's build.

No root/system install, daemon start, camera access or model download. The Arch
plugin archives are pinned to SHA256 recorded from the official repository DB
fetched over HTTPS; this is not a claim of a separately verified PGP signature.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'build/reachy-webrtc'
DOWNLOADS=ROOT/'downloads/reachy-webrtc'
DEBS={
 'gir1.2-gst-plugins-bad-1.0':('1.28.2-1ubuntu1.2','a7b2f4e0241270ec2442ab07dfd988b47d68ea551588ae446ee92d7c973d869a'),
 'gstreamer1.0-nice':('0.1.23-2','1afbf9452d968af7c08e053c895e59f13283962b44a9ff82dc39984a9275c8a8'),
 'gstreamer1.0-plugins-bad':('1.28.2-1ubuntu1.2','9cde564df8b595a09acc19223ac470f567494536e79615746241a8a969c33c06'),
 'libgstreamer-plugins-bad1.0-0':('1.28.2-1ubuntu1.2','3c020133ddf4483470abb7fedc9b5ac7dc14378951a8297b068bb739286f1c3b'),
 'libgupnp-igd-1.6-0':('1.6.0-5','4107a888870e37e40fc6524b09340b828d81395a4b1e549e49086adcc7e7ab45'),
 'libnice10':('0.1.23-2','e97f70468d7b432cf3c0cc448ead774a7156cda58c56efcf727e80a5a6a39135'),
 'libsrtp2-1':('2.7.0-3build1','764fab831b23446339dd58f381086fc81f0f2f3c5f2396fa71a5dd332ee7dd68'),
 'libusrsctp2':('0.9.5.0-2build2','b8a1921a17d978a738383ec8362247940d5d0311221377fabd50d2170563a7f6'),
}
ARCH={
 'gst-plugin-rsrtp-0.15.4-2-x86_64.pkg.tar.zst':'4de105c05eb21f6535c654210ced35d1c03d3d0c711a4ea6280db00d6b0202e6',
 'gst-plugin-rswebrtc-0.15.4-2-x86_64.pkg.tar.zst':'f33128b4b3f1ac63b421b2d0ba2cb89963df13ef0f6e30946ab7e423a950acd2',
}
PLUGINS=('webrtc','nice','dtls','srtp','sctp','debugutilsbad')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def environment(parent=None):
    env=dict(os.environ if parent is None else parent)
    libs=str(OUT/'sysroot/usr/lib/x86_64-linux-gnu')
    env['LD_LIBRARY_PATH']=libs+(':'+env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    env['GST_PLUGIN_PATH']=str(OUT/'plugins')
    env['GST_REGISTRY_1_0']=str(OUT/'curated-registry.bin')
    env['GI_TYPELIB_PATH']=str(OUT/'sysroot/usr/lib/x86_64-linux-gnu/girepository-1.0')
    env['WEBRTCSINK_SIGNALLING_SERVER_LOG']='warn'
    return env


def main():
    subprocess.run(['findmnt','-n',os.environ.get('CAMMY_WORK_MOUNT', '/')],check=True,stdout=subprocess.DEVNULL)
    if 'VERSION_ID="26.04"' not in Path('/etc/os-release').read_text():
        raise RuntimeError('This pinned dependency profile targets Ubuntu26.04 x86_64')
    debdir=DOWNLOADS/'debs';debdir.mkdir(parents=True,exist_ok=True)
    (OUT/'sysroot').mkdir(parents=True,exist_ok=True)
    archives=[]
    for name,(version,expected) in DEBS.items():
        file=debdir/f'{name}_{version}_amd64.deb'
        if not file.exists():subprocess.run(['apt-get','download',name+'='+version],cwd=debdir,check=True)
        if digest(file)!=expected:raise ValueError('Pinned package mismatch: '+name)
        subprocess.run(['dpkg-deb','-x',str(file),str(OUT/'sysroot')],check=True)
        archives.append({'file':str(file.relative_to(ROOT)),'sha256':expected})
    for name,expected in ARCH.items():
        file=DOWNLOADS/name
        if not file.exists():
            urllib.request.urlretrieve('https://geo.mirror.pkgbuild.com/extra/os/x86_64/'+name,file)
        if digest(file)!=expected:raise ValueError('Pinned Arch plugin mismatch')
        with tarfile.open(file,'r:zst') as archive:
            archive.extractall(OUT/'arch',filter='data',members=[m for m in archive.getmembers()
                if m.name.startswith(('usr/lib/gstreamer-1.0/','usr/share/licenses/'))])
        archives.append({'file':str(file.relative_to(ROOT)),'sha256':expected})
    links=OUT/'plugins';links.mkdir(exist_ok=True)
    wanted=[OUT/f'sysroot/usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgst{x}.so' for x in PLUGINS]
    wanted.extend(OUT/f'arch/usr/lib/gstreamer-1.0/libgstrs{x}.so' for x in ('webrtc','rtp'))
    for file in wanted:
        link=links/file.name
        if not file.is_file():raise RuntimeError('Missing required plugin: '+file.name)
        if link.exists() or link.is_symlink():
            if not link.is_symlink() or link.resolve()!=file.resolve():raise ValueError('Unexpected plugin link')
        else:link.symlink_to(file.resolve())
    if {p.name for p in links.iterdir()}!={p.name for p in wanted}:
        raise ValueError('Unexpected plugin in curated search path')
    manifest={'profile':'Ubuntu26.04/Arch rswebrtc0.15.4 project-local','archives':archives,
              'plugins':{p.name:digest(p) for p in wanted},'system_installed':False}
    (OUT/'dependency-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'prepared':True,'plugins':len(wanted),'system_installed':False}))


if __name__=='__main__':main()
