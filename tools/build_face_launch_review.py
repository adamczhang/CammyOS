"""Build a DISARMED local review kit for one finite GUI face trial.

No device, card, install, reboot or launch actions. Extends the existing boot
helper only by explicit profile constants; its state/rollback logic is unchanged.
The old status-only helper and classifier remain untouched.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tools'))
from boot_launch.generator import generate_proposal,SAVED_STOCK_S01APP,HELPER_PROPOSAL_PATH
from native_trial import records as R
from tools import build_native_face_candidate as F

BASE_SHA='ea98f04f3c35fe3cdc70a9e1196926fd3ef1b4b45f71736669c65ea91d457e9c'
CLASS='gui-face-trial-v1'
GATE='face-trial-authorized'
CHANGES=(('LIBROOT=/tmp/cammy-native-status\n','LIBROOT=/tmp/cammy-face\n'),
         ('LIBNAME=libcammy_native_status.so\n','LIBNAME=face.so\n'),
         ('status-only|media-lease-gated)',CLASS+'|media-lease-gated)'),
         ('\t\tstatus-only) ;;','\t\t'+CLASS+') ;;'))
def sha(data):return hashlib.sha256(data).hexdigest()
def md5(data):return hashlib.md5(data).hexdigest()


def profile_helper():
    raw=HELPER_PROPOSAL_PATH.read_bytes()
    if sha(raw)!=BASE_SHA:raise ValueError('Boot helper differs from reviewed source')
    text=raw.decode('ascii')
    for old,new in CHANGES:
        if text.count(old)!=1:raise ValueError('Profile context differs')
        text=text.replace(old,new)
    text=text.replace('chunk5-fixture-open',GATE)
    reverse=text.replace(GATE,'chunk5-fixture-open')
    for old,new in reversed(CHANGES):reverse=reverse.replace(new,old)
    if reverse.encode()!=raw:raise ValueError('Profile escaped constant-only transformation')
    return text.encode()


def proposal():
    original=SAVED_STOCK_S01APP.read_bytes()
    built=generate_proposal(original,profile_helper())
    if built.replacement_text.count('\nSELECT_GATE=blocked\n')!=1:raise ValueError('Gate count differs')
    # Reviewable live-profile script; remains uninstalled and disarmed by absent
    # enable/arm files. It is NOT the synthetic fixture gate.
    text=built.replacement_text.replace('\nSELECT_GATE=blocked\n','\nSELECT_GATE='+GATE+'\n')
    return built,text.encode('ascii')


def build(candidate_dir,out):
    candidate_dir=Path(candidate_dir).resolve(strict=True);out=Path(out).absolute()
    if any(p.is_symlink() for p in (out,*out.parents)):raise ValueError('Symlink output refused')
    if not any(out.is_relative_to(p) for p in [ROOT/'private/native-face',ROOT/'build/native-face']):raise ValueError('Ignored native-face output required')
    if out.exists():raise ValueError('Refusing overwrite')
    source=F.SOURCE.read_bytes();library=(candidate_dir/'face.so').read_bytes()
    candidate=(candidate_dir/'sdvcam-face.candidate').read_bytes()
    manifest=json.loads((candidate_dir/'manifest.json').read_text())
    expected,scope=F.patch(source,sha(library))
    if candidate!=expected or manifest['class']!=CLASS or manifest['library_sha256']!=sha(library):raise ValueError('Candidate not exact reproduced GUI profile')
    if manifest['trial_delay_ms']!=60000 or manifest['trial_duration_ms']!=10000:raise ValueError('Require60s delay/10s visibility')
    abi=F.library_audit(candidate_dir/'face.so',ROOT/'build/native-loader/device-rootfs',F.EXPORTS)
    if not abi['pass']:raise ValueError('Device ABI audit failed')
    base,s01=proposal();attempt=sha(candidate+library+s01)[:32]
    values={'format':R.ALLOW_FORMAT,'s01app_md5':md5(s01),'source_md5':md5(source),'source_size':str(len(source)),
        'candidate':f'{md5(candidate)}:{len(candidate)}:{CLASS}',
        'library':f'{sha(library)}:{md5(library)}:{len(library)}'}
    allow=R.render(R.ALLOW_KEYS,values)
    arm=R.arm_record(attempt,md5(candidate),len(candidate),CLASS,sha(library),md5(library),len(library))
    files={'internal-bootstrap/S01app.proposal':s01,'internal-bootstrap/native-select.allow':allow,
        'internal-bootstrap/native-select.enabled':b'mode=disabled\n',
        'templates/native-select.enabled.select-template':b'mode=select\n','templates/arm.template':arm,
        'tf-staging/CammyOS-native/candidate.bin':candidate,'tf-staging/CammyOS-native/face.so':library,
        'rollback/S01app.stock':SAVED_STOCK_S01APP.read_bytes(),
        'provenance/helper-profile.proposal':profile_helper(),'provenance/face-manifest.json':(candidate_dir/'manifest.json').read_bytes()}
    transaction=(ROOT/'native/activation/stock_bootstrap_transaction.review.sh').read_text()
    for prefix,value in [('NEXT_MD5=',md5(s01)),('NEXT_SIZE=',str(len(s01)))]:
        lines=transaction.splitlines(keepends=True);hits=[i for i,line in enumerate(lines) if line.startswith(prefix)]
        if len(hits)!=1:raise ValueError('Transaction identity context differs')
        lines[hits[0]]=prefix+value+'\n';transaction=''.join(lines)
    files['internal-bootstrap/transaction.review.sh']=transaction.encode()
    out.mkdir(parents=True,mode=0o700)
    for name,data in files.items():
        p=out/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);p.chmod(0o600)
    report=dict(class_=CLASS,verdict='DISARMED_REVIEW_ONLY',installed=False,installation_authorized=False,
        arm_file_present=False,mode='disabled',live_profile_gate=GATE,attempt=attempt,
        candidate=manifest,preserved_stock_ranges=base.preserved_ranges,library_abi=abi,
        bootstrap_changes='same five stock launch sites and tightened backup gate; constant-only GUI profile',
        unchanged_helper_algorithm=True,base_helper_sha256=BASE_SHA,
        open_gates=['new-class independent activation review','explicit internal-write approval','physical LCD/restore acceptance','concurrent physical LCD plus A/V acceptance'],
        files={name:dict(sha256=sha(data),md5=md5(data),bytes=len(data)) for name,data in files.items()})
    (out/'review-manifest.json').write_text(json.dumps(report,indent=2)+'\n');(out/'review-manifest.json').chmod(0o600)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();r=build(args.candidate,args.out);print(json.dumps({k:r[k] for k in ['verdict','installed','mode','attempt']}))
