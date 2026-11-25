"""Offline integrity/provenance review only. Never installs, arms or contacts hardware."""
import argparse,hashlib,json,os,stat,sys
from pathlib import Path,PurePosixPath
from tools import rc06_build as B, rc_wifi_policy, rc_runtime_integrity as I
from tools.boot_launch.generator import generate_proposal,SAVED_STOCK_S01APP
from tools import build_native_face_candidate as F

def validate(root):
    root=Path(root).absolute()
    if root.is_symlink() or not root.is_dir():raise ValueError('Real package directory required')
    manifest=root/'candidate.json'
    if manifest.is_symlink() or not manifest.is_file():raise ValueError('Real manifest required')
    report=json.loads(manifest.read_text())
    if any(report.get(k) is not False for k in ['deployed','activation_eligible','hardware_accepted','resident_opt_in','automatic_media_initialization','native_animation_hardware_verified','speaker_wifi_hardware_verified']):raise ValueError('Unaccepted candidate flags differ')
    if report.get('power_retry_opt_in') is not False or report.get('per_boot_tf_writes') is not True:raise ValueError('Power recovery candidate flags differ')
    if report.get('format')!='cammy-integrated-candidate/1' or report.get('version')!=B.VERSION or report.get('class')!=B.CLASS or report.get('default_mode')!='disabled':raise ValueError('Candidate profile differs')
    rid=report.get('release_id','')
    if len(rid)!=24 or any(c not in '0123456789abcdef' for c in rid):raise ValueError('Invalid release id')
    slot='CammyOS-releases/'+rid
    if report.get('slot')!=slot:raise ValueError('Invalid slot')
    seed=report.get('build_seed',{})
    if seed.get('builder')!=B.sha((B.ROOT/'tools/rc06_build.py').read_bytes()) or seed.get('sources')!=B.source_identities():raise ValueError('Build source receipt is stale or altered')
    if B.sha(json.dumps(seed,sort_keys=True).encode())[:24]!=rid:raise ValueError('Release id differs from source receipt')
    files=report['files'];actual=set()
    rc_wifi_policy.validate_members(files, slot)
    for p in root.rglob('*'):
        st=p.lstat()
        if stat.S_ISLNK(st.st_mode):raise ValueError('Package symlink')
        if stat.S_ISDIR(st.st_mode):continue
        if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_mode&0o177:raise ValueError('Private non-executable single-link files required')
        actual.add(str(p.relative_to(root)))
    if actual!=set(files)|{'candidate.json'}:raise ValueError('Missing or unexpected package member')
    data={}
    for name,record in files.items():
        path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or str(path)!=name:raise ValueError('Invalid member path')
        raw=(root/name).read_bytes()
        if record!={'sha256':B.sha(raw),'bytes':len(raw)}:raise ValueError('Member integrity mismatch: '+name)
        if name.startswith('tf/'):
            rc_wifi_policy.validate_tf_content(raw)
        data[name]=raw
    rc_wifi_policy.validate_install_plan(json.loads(data['installation-plan.json']), rid)
    hp=B.helper(slot)
    expected=generate_proposal(SAVED_STOCK_S01APP.read_bytes(),hp).replacement_text
    expected=B.replace_once(expected,'\nSELECT_GATE=blocked\n','\nSELECT_GATE='+B.GATE+'\n').encode()
    expected_s01=expected
    prefix='tf/'+slot+'/runtime/'
    if seed.get('speaker_guardian')!=B.sha(data[prefix+'speaker-guardian']) or seed.get('portal')!=B.sha(data[prefix+'cammy-portal']) or seed.get('candidate')!=report.get('candidate_sha256') or seed.get('library')!=report.get('library_sha256'):raise ValueError('Binary receipt differs')
    if data['bootstrap/S01app']!=expected or data[prefix+'boot-helper.sh']!=hp:raise ValueError('Launch source provenance differs')
    runtime={name[len(prefix):]:raw for name,raw in data.items() if name.startswith(prefix)}
    if data[prefix+'startup.sh']!=I.ram_runtime(B.runtime(slot),set(runtime)):raise ValueError('Runtime source differs')
    if data['rollback/S01app.stock']!=SAVED_STOCK_S01APP.read_bytes():raise ValueError('Stock rollback differs')
    if data['rollback/S99cammy-tf.previous']!=(B.ROOT/'scripts/S99cammy-tf').read_bytes():raise ValueError('Previous runtime rollback differs')
    if data['bootstrap/native-select.enabled']!=b'mode=disabled\n':raise ValueError('Armed bootstrap')
    if any('/ack-' in name or (name.startswith('bootstrap/') and PurePosixPath(name).name in
           {'native-select.resident','native-select.retry','native-select.retry-used','native-select.crash-used'}) for name in data):
        raise ValueError('Unexpected live authorization')
    library=root/('tf/'+slot+'/native/face.so');raw=library.read_bytes()
    face_bytes=len(raw)+B.FACE_DECODE_BYTES
    if seed.get('face_bytes')!=face_bytes or face_bytes>B.FACE_LIBRARY_BUDGET:raise ValueError('Face RAM budget differs')
    art=seed.get('cammy_face',{})
    if (not art.get('available') or art.get('atlas_sha256')!=B.sha(B.cammy_pack.ATLAS.read_bytes())
            or art.get('sleep_sha256')!=B.sha(B.cammy_pack.SLEEP.read_bytes())):raise ValueError('Cammy face art receipt differs')
    expected_candidate,scope=F.patch(F.SOURCE.read_bytes(),B.sha(raw))
    if data['tf/'+slot+'/native/candidate.bin']!=expected_candidate or report.get('candidate_sha256')!=B.sha(expected_candidate) or report.get('library_sha256')!=B.sha(raw):raise ValueError('Native exact patch differs')
    audit=F.library_audit(library,B.ROOT/'build/native-loader/device-rootfs',F.EXPORTS,profile='face-service')
    if not audit['pass']:raise ValueError('Device ABI differs')
    for name,template in [('rc-health.sh','health.sh'),('rc-media-worker.sh','media-worker.sh')]:
        expected=I.rc_script(name,(B.ROOT/'scripts/rc06'/template).read_text(),'/mnt/extsd/'+slot+'/runtime')
        if data[prefix+name]!=expected:raise ValueError('Health/media source differs')
    if data[prefix+'native-media-start.sh']!=B.media_script(B.sha(expected_candidate),B.md5(expected_candidate)):raise ValueError('Media owner binding differs')
    # Checks the manifest too: bootstrap_s99 refuses one that differs from its members.
    s99=I.bootstrap_s99((B.ROOT/'scripts/S99cammy-tf').read_text(),'/mnt/extsd/'+slot+'/runtime',runtime, require_candidate=True)
    if data['bootstrap/S99cammy-tf']!=s99.encode():raise ValueError('S99 bootstrap provenance differs')
    source=F.SOURCE.read_bytes()
    values={'format':B.R.ALLOW_FORMAT,'s01app_md5':B.md5(expected_s01),'source_md5':B.md5(source),'source_size':str(len(source)),
            'candidate':f'{B.md5(expected_candidate)}:{len(expected_candidate)}:{B.CLASS}','library':f'{B.sha(raw)}:{B.md5(raw)}:{len(raw)}'}
    if data['bootstrap/native-select.allow']!=B.R.render(B.R.ALLOW_KEYS,values):raise ValueError('Native allowlist differs')
    resident=f'format=cammy-resident/1\nrelease_id={rid}\n'.encode()
    retry=f'format=cammy-power-retry/1\nrelease_id={rid}\ncandidate_md5={B.md5(expected_candidate)}\n'.encode()
    if data.get('templates/native-select.resident')!=resident or data.get('templates/native-select.retry')!=retry:
        raise ValueError('Power policy template differs')
    attempt=B.sha(expected_candidate+raw+expected_s01)[:32]
    if report.get('attempt')!=attempt or data['tf/'+slot+'/native/arm']!=B.R.arm_record(attempt,B.md5(expected_candidate),len(expected_candidate),B.CLASS,B.sha(raw),B.md5(raw),len(raw)):raise ValueError('One-shot arm differs')
    from tools.speaker_profile import profile as speaker_profile
    if data[prefix+'speaker.conf']!=speaker_profile().encode():raise ValueError('Speaker profile differs')
    if (not data[prefix+'fault-evidence'].startswith(b'\x7fELF') or
            seed.get('fault_evidence') != B.sha(data[prefix+'fault-evidence'])):
        raise ValueError('Fault evidence helper differs')
    binaries={name[len(prefix):]:B.sha(raw) for name,raw in data.items()
              if name.startswith(prefix) and raw.startswith(b'\x7fELF')}
    if seed.get('device_binaries')!=binaries:raise ValueError('Binary receipt differs')
    if not data[prefix+'speaker-guardian'].startswith(b'\x7fELF'):raise ValueError('Speaker guardian ELF missing')
    if b'--speaker-root \"$R\"' not in data[prefix+'startup.sh']:raise ValueError('Speaker runtime not wired')
    # Loading gate: cammy-ui starts once the payload is verified, ahead of the stock settle.
    if data[prefix+'startup.sh'].count(b'\nstart_ui\nUI_STARTS=1\nphase stock_settle\n')!=1:raise ValueError('Loading gate UI start not wired')
    checksum=''.join(B.sha(raw)+'  '+name[len(prefix):]+'\n' for name,raw in sorted(data.items()) if name.startswith(prefix) and name!=prefix+'SHA256SUMS').encode()
    if data[prefix+'SHA256SUMS']!=checksum:raise ValueError('Runtime checksum list differs')
    controls=json.loads(data['controls/m105.json'])
    if controls['gimbal']['tracking']['remote_toggle_verified'] or controls['voice_preference']['activated_by_cammy']:raise ValueError('Unverified capabilities promoted')
    return {'ok':True,'verdict':'LOCAL_CANDIDATE_HARDWARE_ACCEPTANCE_PENDING','release_id':rid,'version':B.VERSION,'files':len(data),'activation_eligible':False,'hardware_accepted':False}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);a=p.parse_args()
    try:print(json.dumps(validate(a.directory),indent=2))
    except (ValueError,KeyError,OSError,json.JSONDecodeError) as e:p.exit(2,str(e)+'\n')
