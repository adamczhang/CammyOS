"""Selector power-loss retry for the rc06 boot helper. Synthetic fake tree only:
no camera, card, mount, reboot or power test. Behaviour runs under dash with the
fake tree's PATH stubs. Host BusyBox ash prefers its own applets over PATH, so it
cannot run those stubs; for BusyBox the generated scripts are parsed by ash and
every external command the new code uses is checked against the stock applets."""
import hashlib,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tools')]
from tools.rc06_build import helper,GATE,CLASS
from tools.boot_launch.generator import SAVED_STOCK_S01APP
from tools.boot_launch.model import (BootLaunchFakeTree,relocate_helper_for_test,
    SYNTHETIC_CANDIDATE_BYTES as CAND,SYNTHETIC_LIBRARY_BYTES as LIB,SYNTHETIC_INIT_BYTES as INIT)
from tools.native_trial import records as R

B1='00000001-0000-4000-8000-000000000001'
B2='00000002-0000-4000-8000-000000000002'
B3='00000003-0000-4000-8000-000000000003'
B4='00000004-0000-4000-8000-000000000004'
B5='00000005-0000-4000-8000-000000000005'
ATT='a'*32
RELEASE='0123456789abcdef01234567'
def md5(b):return hashlib.md5(b).hexdigest()
def sha(b):return hashlib.sha256(b).hexdigest()

def stock_applets():
    """Names the stock rootfs links to its BusyBox 1.27.2 binary."""
    rootfs=SAVED_STOCK_S01APP.parents[2]
    names=set()
    for folder in ['bin','sbin','usr/bin','usr/sbin']:
        d=rootfs/folder
        if not d.is_dir():continue
        for p in d.iterdir():
            if p.is_symlink() and os.readlink(p).endswith('busybox'):names.add(p.name)
    return names

HOST_BUSYBOX=shutil.which('busybox')

class PowerRetrySelectorTests(unittest.TestCase):
    shell='dash'
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='cammy-power-retry-');self.addCleanup(tmp.cleanup)
        self.base=Path(tmp.name)
        self.path_tail='/usr/bin:/bin'
        self.tree=self.make_tree()

    def make_tree(self,boot=B2,mode='mode=select\n'):
        tree=BootLaunchFakeTree.build(self.base/'tree',boot_id=boot,enabled_content=mode)
        sm,ss=tree.synthetic_stock_identity()
        tree.path('/etc/cammy/native-select.allow').write_bytes(R.render(R.ALLOW_KEYS,{'format':R.ALLOW_FORMAT,
            's01app_md5':md5(INIT),'source_md5':sm,'source_size':str(ss),
            'candidate':f'{md5(CAND)}:{len(CAND)}:{CLASS}','library':f'{sha(LIB)}:{md5(LIB)}:{len(LIB)}'}))
        native=tree.card/'synthetic/native';native.mkdir(parents=True)
        (native/'arm').write_bytes(R.arm_record(ATT,md5(CAND),len(CAND),CLASS,sha(LIB),md5(LIB),len(LIB)))
        (native/'candidate.bin').write_bytes(CAND);(native/'face.so').write_bytes(LIB)
        self.cfg=tree.path('/etc/cammy')
        return tree

    # ---- fixtures for the interrupted boot ----
    # The older retry rules apply only without a resident release (with one,
    # cammy_resident_recover decides; see ResidentRecoveryTests).
    resident=False
    def interrupted(self,boot=B1,phase='idle_ready',optin=True):
        state=self.tree.state_dir;state.mkdir(mode=0o700,exist_ok=True)
        (state/('claimed-'+ATT)).write_bytes(R.claim_record(ATT,boot,md5(CAND)))
        (self.cfg/'startup.pending').write_text(f'phase={phase}\nboot_id={boot}\n')
        if self.resident:
            (self.cfg/'native-select.resident').write_text(f'format=cammy-resident/1\nrelease_id={RELEASE}\n')
        if optin:
            (self.cfg/'native-select.retry').write_text(f'format=cammy-power-retry/1\nrelease_id={RELEASE}\ncandidate_md5={md5(CAND)}\n')

    def boot(self):
        text=relocate_helper_for_test(helper('synthetic').decode(),self.tree,select_gate=GATE)
        script=self.tree.ctl/'boot.sh';script.write_text(text+'\ncammy_prepare_launch\ncammy_launch_selected_or_stock\nwait\n')
        argv=['dash',str(script)]
        return subprocess.run(argv,env={'PATH':str(self.tree.ctl/'bin')+':'+self.path_tail,'LC_ALL':'C'},
            cwd=self.tree.root,capture_output=True,text=True,timeout=30)

    def decision(self,result):
        for line in result.stdout.splitlines():
            if line.startswith('cammy-launch: decision='):
                return dict(f.split('=',1) for f in line.split()[1:])
        self.fail('no decision: '+result.stdout+result.stderr)

    def durable(self):
        """Every internal record that the retry may touch, by relative path."""
        out={}
        for p in sorted(self.cfg.rglob('*')):
            if p.is_file() or p.is_symlink():
                rel=str(p.relative_to(self.cfg))
                if rel.startswith('native-select.status') or rel.startswith('boot-launch'):continue
                out[rel]=p.read_bytes() if p.is_file() and not p.is_symlink() else ('link',os.readlink(p))
        return out

    def assert_stock_unchanged(self,reason='previous-runtime-fault'):
        before=self.durable()
        launched=len(self.tree.launched_entries());calls=len(self.tree.calls())
        result=self.boot();self.assertEqual(result.returncode,0,result.stderr)
        d=self.decision(result)
        self.assertEqual((d['decision'],d['reason']),('stock',reason))
        self.assertEqual(self.tree.launched_entries()[launched:],['sdvcam-stub'])
        self.assertFalse(any(c.startswith('mount ') for c in self.tree.calls()[calls:]))
        self.assertEqual(self.durable(),before)

    # ---- eligible retry ----
    # ---- fail-closed matrix: stock, nothing moved, no card access ----
    def test_without_opt_in_stays_stock(self):
        self.interrupted(optin=False);self.assert_stock_unchanged()

    def test_early_or_fault_phases_stay_stock(self):
        for phase in ['launch','payload','stock_settle','radio_load','station_setup','dhcp','portal','menu',
                      'ap_fallback','media_prepare','runtime_health_fault','idle_ready_extra','IDLE_READY']:
            with self.subTest(phase=phase):
                self.reset();self.interrupted(phase=phase);self.assert_stock_unchanged()

    def test_malformed_or_ambiguous_records_stay_stock(self):
        g=self.cfg/'startup.pending'
        cases={
            'guard-same-boot':lambda:g.write_text(f'phase=idle_ready\nboot_id={B2}\n'),
            'guard-no-newline':lambda:g.write_text(f'phase=idle_ready\nboot_id={B1}'),
            'guard-extra-line':lambda:g.write_text(f'phase=idle_ready\nboot_id={B1}\nx=1\n'),
            'guard-swapped':lambda:g.write_text(f'boot_id={B1}\nphase=idle_ready\n'),
            'guard-empty':lambda:g.write_text(''),
            'guard-truncated':lambda:g.write_text('phase=idle_re'),
            'guard-bad-boot':lambda:g.write_text('phase=idle_ready\nboot_id=unknown\n'),
            'guard-directory':lambda:(g.unlink(),g.mkdir()),
            'fault-record':lambda:(self.cfg/'runtime-health-fault').write_text('format=cammy-runtime-health-fault/1\n'),
            'fault-record-link':lambda:(self.cfg/'runtime-health-fault').symlink_to('/nonexistent'),
            'claim-other-boot':lambda:(self.tree.state_dir/('claimed-'+ATT)).write_bytes(R.claim_record(ATT,B3,md5(CAND))),
            'claim-other-md5':lambda:(self.tree.state_dir/('claimed-'+ATT)).write_bytes(R.claim_record(ATT,B1,'e'*32)),
            'claim-malformed':lambda:(self.tree.state_dir/('claimed-'+ATT)).write_bytes(b'format=cammy-native-claim/1\n'),
            'no-claim':lambda:(self.tree.state_dir/('claimed-'+ATT)).unlink(),
            'two-claims':lambda:(self.tree.state_dir/('claimed-'+'b'*32)).write_bytes(R.claim_record('b'*32,B1,md5(CAND))),
            'observed':lambda:(self.tree.state_dir/('observed-'+ATT)).write_bytes(R.observed_record(ATT,B2)),
            'inflight':lambda:(self.tree.state_dir/'inflight').write_text(f'format=cammy-boot-launch-inflight/1\nboot={B1}\nmode=select\n'),
            'state-hidden-entry':lambda:(self.tree.state_dir/'.x').write_text('x\n'),
            'optin-other-release':lambda:(self.cfg/'native-select.retry').write_text(f'format=cammy-power-retry/1\nrelease_id=other\ncandidate_md5={md5(CAND)}\n'),
            'optin-other-md5':lambda:(self.cfg/'native-select.retry').write_text(f'format=cammy-power-retry/1\nrelease_id={RELEASE}\ncandidate_md5={"e"*32}\n'),
            'optin-bad-format':lambda:(self.cfg/'native-select.retry').write_text(f'format=cammy-power-retry/2\nrelease_id={RELEASE}\ncandidate_md5={md5(CAND)}\n'),
            'optin-link':lambda:self.relink(self.cfg/'native-select.retry'),
            'resident-other-release':lambda:(self.cfg/'native-select.resident').write_text('format=cammy-resident/1\nrelease_id=other\n'),
            'allow-other-candidate':lambda:self.reallow(),
            'history-link':lambda:(self.cfg/'native-release-history').symlink_to('/tmp'),
            'history-collision':lambda:((self.cfg/'native-release-history').mkdir(),(self.cfg/'native-release-history'/f'startup.pending.retry-{B1}').write_text('x\n')),
        }
        for name,mutate in cases.items():
            with self.subTest(case=name):
                self.reset();self.interrupted();mutate();self.assert_stock_unchanged()

    def test_guard_symlink_stays_stock(self):
        self.interrupted();g=self.cfg/'startup.pending';real=self.cfg/'real-guard';real.write_bytes(g.read_bytes());g.unlink();g.symlink_to(real)
        self.assert_stock_unchanged()

    def test_consumed_or_malformed_ledger_stays_stock(self):
        for content in [f'format=cammy-power-retry-used/1\nattempt={ATT}\nfailed_boot={B1}\nretry_boot={B2}\n','','format=cammy-pow']:
            with self.subTest(content=content):
                self.reset();self.interrupted();(self.cfg/'native-select.retry-used').write_text(content)
                self.assert_stock_unchanged('retry-consumed')
        self.reset();self.interrupted();(self.cfg/'native-select.retry-used').write_text('x\n');self.relink(self.cfg/'native-select.retry-used')
        self.assert_stock_unchanged('retry-consumed')

    def test_observe_mode_never_retries(self):
        self.tree=self.make_tree(mode='mode=observe\n');self.interrupted();self.assert_stock_unchanged()

    def test_firstboot_never_spends_the_retry(self):
        self.interrupted();self.tree.path('/overlay/.firstboot').parent.mkdir(parents=True,exist_ok=True)
        self.tree.path('/overlay/.firstboot').write_text('')
        self.assert_stock_unchanged()

    def abrupt_resident(self, phase, committed):
        self.interrupted(phase=phase, optin=False)
        hist = self.cfg/'native-release-history'
        hist.mkdir()
        claim = self.tree.state_dir/('claimed-'+ATT)
        if committed:
            claim.rename(hist/f'claimed-{ATT}.healthy-{B1}')
        else:
            (hist/f'claimed-{ATT}.healthy-{B4}').write_bytes(R.claim_record(ATT,B4,md5(CAND)))
        (self.cfg/'startup.pending').write_text(
            f'phase={phase}\nboot_id={B1}\nrelease_id={RELEASE}\ncandidate_md5={md5(CAND)}\n')
        self.tree.reboot(B2, unsynced='keep')

    def crashed_resident(self):
        self.interrupted(optin=False)
        (self.tree.state_dir / ('claimed-' + ATT)).unlink()
        (self.cfg/'startup.pending').write_text(
            f'phase=post_healthy_fault\nboot_id={B1}\nrelease_id={RELEASE}\ncandidate_md5={md5(CAND)}\n')
        (self.cfg/'runtime-health-fault').write_text(
            f'format=cammy-runtime-health-fault/1\nboot_id={B1}\nrelease_id={RELEASE}\n'
            'uptime_s=513\nreason=native-pid\nmedia_reason=none\nguard_before=absent\nrearmed=yes\n')
        self.tree.reboot(B2, unsynced='keep')

    # ---- radio-fault retry (boot fbaa812c, approved 2026-11-23) ----
    def radio_faulted(self,boot=B1,release=RELEASE,claim_boot=None):
        state=self.tree.state_dir;state.mkdir(mode=0o700,exist_ok=True)
        (state/('claimed-'+ATT)).write_bytes(R.claim_record(ATT,claim_boot or boot,md5(CAND)))
        (self.cfg/'startup.pending').write_text(f'phase=radio_fault\nboot_id={boot}\nrelease_id={release}\ncandidate_md5={md5(CAND)}\n')
        (self.cfg/'native-select.resident').write_text(f'format=cammy-resident/1\nrelease_id={RELEASE}\n')

    def test_observe_mode_never_spends_the_radio_retry(self):
        shutil.rmtree(self.base/'tree');self.tree=self.make_tree(mode='mode=observe\n')
        self.radio_faulted()
        result=self.boot();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.decision(result)['decision'],'stock')
        self.assertFalse((self.cfg/'native-select.radio-used').exists())
        self.assertTrue((self.cfg/'startup.pending').exists())

    # ---- deterministic power loss at every durable transition ----
    def test_truncated_ledger_after_power_loss_stays_stock(self):
        self.interrupted();(self.cfg/'native-select.retry-used').write_text('format=cammy-power-retry-used/1\natt')
        self.assert_stock_unchanged('retry-consumed')

    # ---- helpers ----
    def relink(self,p):
        real=p.with_name(p.name+'.real');real.write_bytes(p.read_bytes());p.unlink();p.symlink_to(real)
    def reallow(self):
        a=self.cfg/'native-select.allow';t=a.read_text().replace(md5(CAND),'e'*32);a.write_text(t)
    def reset(self):
        shutil.rmtree(self.base/'tree');self.tree=self.make_tree()

def only_own_tests(cls):
    """Run only the tests a subclass defines itself, not the fixture class's."""
    for n in dir(cls):
        if n.startswith('test') and n not in cls.__dict__:setattr(cls,n,None)
    return cls

@only_own_tests
class ResidentRecoveryTests(PowerRetrySelectorTests):
    """A resident release always starts CammyOS (Adam, 2026-11-24). Power-offs and
    crashes after the menu leave records that are archived, not obeyed. Only boots
    that never reached the menu count; three in a row select stock for one boot."""
    resident=True
    def launch(self):
        r=self.boot();self.assertEqual(r.returncode,0,r.stderr);return self.decision(r)
    def fails(self):
        p=self.cfg/'native-select.fails';return p.read_text() if p.exists() else None
    def assert_cleared(self):
        for n in ['startup.pending','runtime-health-fault','native-select.crash-used','native-select.radio-used']:
            self.assertFalse((self.cfg/n).exists(),n)
        keep=self.cfg/'native-select.archived-recover'
        self.assertTrue(keep.is_dir());self.assertEqual(keep.stat().st_mode&0o777,0o700)
    def fault(self,boot=B1,guard='idle',rearmed='no',reason='native-pid'):
        (self.cfg/'runtime-health-fault').write_text(f'format=cammy-runtime-health-fault/1\nboot_id={boot}\nrelease_id={RELEASE}\n'
            f'uptime_s=82\nreason={reason}\nmedia_reason=none\nguard_before={guard}\nrearmed={rearmed}\n')

    def test_after_menu_leftovers_start_cammyos(self):
        cases={
            'power-off-before-commit':lambda:self.interrupted(phase='idle_ready',optin=False),
            'power-off-in-media':lambda:self.interrupted(phase='media_prepare',optin=False),
            'crash-latched':lambda:(self.interrupted(phase='runtime_health_fault',optin=False),self.fault()),
            'post-healthy-fault':lambda:((self.cfg/'startup.pending').write_text(
                f'phase=post_healthy_fault\nboot_id={B1}\nrelease_id={RELEASE}\ncandidate_md5={md5(CAND)}\n'),
                (self.cfg/'native-select.resident').write_text(f'format=cammy-resident/1\nrelease_id={RELEASE}\n'),
                self.fault(guard='absent',rearmed='yes')),
            'spent-crash-retry':lambda:(self.interrupted(phase='idle_ready',optin=False),
                (self.cfg/'native-select.crash-used').write_text('format=cammy-crash-retry-used/1\n'),
                (self.cfg/'native-select.radio-used').write_text('format=cammy-radio-retry-used/1\n')),
        }
        for name,setup in cases.items():
            with self.subTest(case=name):
                self.reset();setup()
                self.assertEqual(self.launch()['decision'],'candidate')
                self.assert_cleared();self.assertIsNone(self.fails())

    def test_nothing_left_behind_starts_without_writes(self):
        (self.cfg/'native-select.resident').write_text(f'format=cammy-resident/1\nrelease_id={RELEASE}\n')
        self.assertEqual(self.launch()['decision'],'candidate')
        self.assertFalse((self.cfg/'native-select.archived-recover').exists())

    def test_three_early_failures_in_a_row_select_stock_once(self):
        self.interrupted(boot=B1,phase='launch',optin=False)
        self.assertEqual(self.launch()['decision'],'candidate')
        self.assertIn('count=1\n',self.fails())
        # The same failed boot is counted once.
        self.tree.reboot(B3,unsynced='keep');(self.cfg/'startup.pending').write_text(f'phase=launch\nboot_id={B1}\n')
        d=self.launch();self.assertEqual(d['decision'],'candidate',d);self.assertIn('count=1\n',self.fails())
        self.tree.reboot(B4,unsynced='keep');(self.cfg/'startup.pending').write_text(f'phase=radio_fault\nboot_id={B3}\n')
        self.assertEqual(self.launch()['decision'],'candidate');self.assertIn('count=2\n',self.fails())
        self.tree.reboot(B5,unsynced='keep');(self.cfg/'startup.pending').write_text(f'phase=launch\nboot_id={B4}\n')
        d=self.launch();self.assertEqual((d['decision'],d['reason']),('stock','resident-fail-limit'))
        self.assertIsNone(self.fails());self.assert_cleared()
        # The stock boot leaves nothing, so the next boot is CammyOS again.
        self.tree.reboot(B2,unsynced='keep')
        self.assertEqual(self.launch()['decision'],'candidate')

    def test_early_fault_record_counts(self):
        self.interrupted(phase='runtime_health_fault',optin=False);self.fault(guard='unexpected')
        self.assertEqual(self.launch()['decision'],'candidate');self.assertIn('count=1\n',self.fails())

    def test_reaching_the_menu_resets_the_count(self):
        from tools.rc06_build import runtime
        text=runtime('synthetic').decode()
        i=text.index('Reached the menu');self.assertIn('rm -f /etc/cammy/native-select.fails',text[i:i+120])

    def test_card_is_never_written_by_recovery(self):
        self.interrupted(optin=False)
        before={str(p.relative_to(self.tree.card)):p.read_bytes() for p in self.tree.card.rglob('*') if p.is_file()}
        self.assertEqual(self.launch()['decision'],'candidate')
        after={str(p.relative_to(self.tree.card)):p.read_bytes() for p in self.tree.card.rglob('*') if p.is_file()}
        self.assertEqual(after,before)

    def test_malformed_resident_record_keeps_the_older_rules(self):
        self.interrupted(optin=False);(self.cfg/'native-select.resident').write_text('format=cammy-resident/1\nrelease_id=zz\n')
        self.assertEqual(self.launch()['decision'],'stock')

class StockShellAuditTests(unittest.TestCase):
    def generated(self):
        from tools.rc06_build import runtime
        return {'boot-helper.sh':helper('synthetic').decode(),'startup.sh':runtime('synthetic').decode(),
                'rc-health.sh':(ROOT/'scripts/rc06/health.sh').read_text(),'rc-media-worker.sh':(ROOT/'scripts/rc06/media-worker.sh').read_text()}
    def test_generated_scripts_parse_under_busybox_ash(self):
        if not HOST_BUSYBOX:self.skipTest('host busybox unavailable')
        for name,text in self.generated().items():
            with self.subTest(name=name):
                r=subprocess.run([HOST_BUSYBOX,'sh','-n'],input=text,capture_output=True,text=True)
                self.assertEqual(r.returncode,0,r.stderr)
    def test_new_code_uses_only_stock_applets_and_options(self):
        applets=stock_applets()
        if not applets:self.skipTest('stock rootfs applet list unavailable')
        # External commands in the retry, commit and atomic flag paths (builtins excluded).
        used={'mv','sync','rm','mkdir','cat','sed','cut','wc','tail','awk','readlink','md5sum','pidof'}
        self.assertEqual(sorted(used-applets),[])
        retry=(ROOT/'scripts/rc06/selector-retry.sh').read_text()
        texts=[retry,*self.generated().values()]
        import re
        for text in texts:
            code='\n'.join(l for l in text.splitlines() if not l.lstrip().startswith('#'))
            for bad in [r'mv -[Tn]',r'mv --',r'sync -f',r'sync /',r'mkdir -m',r'rm -d',r'realpath',r'stat',r'head']:
                self.assertIsNone(re.search(r'(?<![\w./-])'+bad+r'\b',code),bad)

if __name__=='__main__':unittest.main()
