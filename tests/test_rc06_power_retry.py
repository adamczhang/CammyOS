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
    def interrupted(self,boot=B1,phase='idle_ready',optin=True):
        state=self.tree.state_dir;state.mkdir(mode=0o700,exist_ok=True)
        (state/('claimed-'+ATT)).write_bytes(R.claim_record(ATT,boot,md5(CAND)))
        (self.cfg/'startup.pending').write_text(f'phase={phase}\nboot_id={boot}\n')
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
    def test_opted_in_idle_ready_interruption_retries_once_and_archives_evidence(self):
        self.interrupted()
        guard=(self.cfg/'startup.pending').read_bytes();claim=(self.tree.state_dir/('claimed-'+ATT)).read_bytes()
        result=self.boot();self.assertEqual(result.returncode,0,result.stderr)
        d=self.decision(result)
        self.assertEqual((d['decision'],d['reason'],d['attempt']),('candidate','selected',ATT))
        self.assertEqual(self.tree.launched_entries(),['candidate-stub'])
        hist=self.cfg/'native-release-history'
        self.assertEqual((hist/f'claimed-{ATT}.retry-{B1}').read_bytes(),claim)
        self.assertEqual((hist/f'startup.pending.retry-{B1}').read_bytes(),guard)
        self.assertFalse((self.cfg/'startup.pending').exists())
        self.assertEqual((self.cfg/'native-select.retry-used').read_text(),
            f'format=cammy-power-retry-used/1\nattempt={ATT}\nfailed_boot={B1}\nretry_boot={B2}\n')
        self.assertEqual((self.tree.state_dir/('claimed-'+ATT)).read_bytes(),R.claim_record(ATT,B2,md5(CAND)))
        # The ledger is synced before either rename, each rename before the next step.
        calls=self.tree.calls();self.assertGreaterEqual(calls.count('sync'),3)
        # The retried boot is also interrupted in late startup: no second retry.
        (self.cfg/'startup.pending').write_text(f'phase=idle_ready\nboot_id={B2}\n')
        self.tree.reboot(B3,unsynced='keep')
        self.assert_stock_unchanged('retry-consumed')

    def test_card_is_never_written_by_a_retry(self):
        self.interrupted()
        before={str(p.relative_to(self.tree.card)):p.read_bytes() for p in self.tree.card.rglob('*') if p.is_file()}
        self.assertEqual(self.decision(self.boot())['decision'],'candidate')
        after={str(p.relative_to(self.tree.card)):p.read_bytes() for p in self.tree.card.rglob('*') if p.is_file()}
        self.assertEqual(after,before)

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
            'resident-missing':lambda:(self.cfg/'native-select.resident').unlink(),
            'resident-link':lambda:self.relink(self.cfg/'native-select.resident'),
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

    def test_abrupt_resident_evidence_gates(self):
        for phase, committed, broken in [
            ('idle_ready',False,'claim'), ('media_prepare',True,'history'),
            ('idle_ready',False,'identity'), ('media_prepare',False,'fault')
        ]:
            with self.subTest(phase=phase, committed=committed, broken=broken):
                self.reset()
                self.abrupt_resident(phase, committed)
                if broken == 'claim':
                    (self.tree.state_dir/('claimed-'+ATT)).write_bytes(R.claim_record(ATT,B3,md5(CAND)))
                elif broken == 'history':
                    (self.cfg/'native-release-history'/f'claimed-{ATT}.healthy-{B1}').unlink()
                elif broken == 'fault':
                    (self.cfg/'runtime-health-fault').write_text('observed native fault\n')
                else:
                    (self.cfg/'native-select.resident').write_text('format=cammy-resident/1\nrelease_id='+'f'*24+'\n')
                self.assert_stock_unchanged()

    def test_abrupt_resident_at_each_write_never_retries_twice(self):
        cases = (('idle_ready',False),('media_prepare',False),('media_prepare',True),('idle_ready',True))
        for phase, committed in cases:
            for cut in range(1, 14):
                with self.subTest(phase=phase, committed=committed, cut=cut):
                    self.reset()
                    self.abrupt_resident(phase, committed)
                    self.tree.set_faults(f'powerloss-at-sync-{cut}')
                    result = self.boot()
                    spent = self.cfg/'native-select.crash-used'
                    self.assertTrue(spent.exists())
                    if not result.returncode:
                        self.assertEqual(self.decision(result)['decision'], 'candidate')
                    self.tree.reboot(B3, unsynced='keep')
                    self.assert_stock_unchanged('crash-retry-spent')
                    if cut == 1:
                        # Losing the uncommitted spend cannot permit two launches.
                        self.assertNotIn('candidate-stub', self.tree.launched_entries())
                        spent.unlink()
                        self.tree.reboot(B5, unsynced='keep')
                        self.assertEqual(self.decision(self.boot())['decision'], 'candidate')
                        self.tree.reboot(B4, unsynced='keep')
                        self.assert_stock_unchanged('crash-retry-spent')
                    if not result.returncode:
                        break

    def crashed_resident(self):
        self.interrupted(optin=False)
        (self.tree.state_dir / ('claimed-' + ATT)).unlink()
        (self.cfg/'startup.pending').write_text(
            f'phase=post_healthy_fault\nboot_id={B1}\nrelease_id={RELEASE}\ncandidate_md5={md5(CAND)}\n')
        (self.cfg/'runtime-health-fault').write_text(
            f'format=cammy-runtime-health-fault/1\nboot_id={B1}\nrelease_id={RELEASE}\n'
            'uptime_s=513\nreason=native-pid\nmedia_reason=none\nguard_before=absent\nrearmed=yes\n')
        self.tree.reboot(B2, unsynced='keep')

    def test_crash_recovery_denies_missing_stale_and_mixed_authority(self):
        for change in ('fault-missing', 'fault-other-reason', 'wrong-release', 'legacy-power-optin', 'claim'):
            with self.subTest(change=change):
                self.reset()
                self.crashed_resident()
                fault = self.cfg/'runtime-health-fault'
                if change == 'fault-missing':
                    fault.unlink()
                elif change == 'fault-other-reason':
                    fault.write_text(fault.read_text().replace('native-pid', 'portal-pid'))
                elif change == 'wrong-release':
                    (self.cfg/'native-select.resident').write_text('format=cammy-resident/1\nrelease_id=' + 'f'*24 + '\n')
                elif change == 'legacy-power-optin':
                    (self.cfg/'native-select.retry').write_text('unexpected legacy authority\n')
                else:
                    (self.tree.state_dir/('claimed-'+ATT)).write_bytes(R.claim_record(ATT,B1,md5(CAND)))
                self.assert_stock_unchanged()

    # ---- radio-fault retry (boot fbaa812c, approved 2026-11-23) ----
    def radio_faulted(self,boot=B1,release=RELEASE,claim_boot=None):
        state=self.tree.state_dir;state.mkdir(mode=0o700,exist_ok=True)
        (state/('claimed-'+ATT)).write_bytes(R.claim_record(ATT,claim_boot or boot,md5(CAND)))
        (self.cfg/'startup.pending').write_text(f'phase=radio_fault\nboot_id={boot}\nrelease_id={release}\ncandidate_md5={md5(CAND)}\n')
        (self.cfg/'native-select.resident').write_text(f'format=cammy-resident/1\nrelease_id={RELEASE}\n')

    def test_radio_fault_retries_once_and_a_second_consecutive_fault_stays_stock(self):
        self.radio_faulted()
        guard=(self.cfg/'startup.pending').read_bytes();claim=(self.tree.state_dir/('claimed-'+ATT)).read_bytes()
        result=self.boot();self.assertEqual(result.returncode,0,result.stderr)
        d=self.decision(result)
        self.assertEqual((d['decision'],d['reason'],d['attempt']),('candidate','selected',ATT))
        self.assertEqual(self.tree.launched_entries(),['candidate-stub'])
        hist=self.cfg/'native-release-history'
        self.assertEqual((hist/f'claimed-{ATT}.radio-{B1}').read_bytes(),claim)
        self.assertEqual((hist/f'startup.pending.radio-{B1}').read_bytes(),guard)
        self.assertEqual((self.cfg/'native-select.radio-used').read_text(),
            f'format=cammy-radio-retry-used/1\nrelease_id={RELEASE}\ncandidate_md5={md5(CAND)}\nfailed_boot={B1}\nretry_boot={B2}\n')
        self.assertGreaterEqual(self.tree.calls().count('sync'),3)
        # The retry boot hits the same radio fault: stock, nothing moved.
        (self.cfg/'startup.pending').write_text(f'phase=radio_fault\nboot_id={B2}\nrelease_id={RELEASE}\ncandidate_md5={md5(CAND)}\n')
        self.tree.reboot(B3,unsynced='keep')
        self.assert_stock_unchanged('radio-retry-spent')

    def test_replenished_radio_ledger_allows_a_later_radio_retry(self):
        self.radio_faulted()
        self.assertEqual(self.decision(self.boot())['decision'],'candidate')
        # Health retired the claim and, after 900 s, the ledger; a later fault retries again.
        (self.tree.state_dir/('claimed-'+ATT)).unlink();(self.cfg/'native-select.radio-used').unlink()
        self.tree.reboot(B3,unsynced='keep')
        (self.tree.state_dir/('claimed-'+ATT)).write_bytes(R.claim_record(ATT,B3,md5(CAND)))
        (self.cfg/'startup.pending').write_text(f'phase=radio_fault\nboot_id={B3}\nrelease_id={RELEASE}\ncandidate_md5={md5(CAND)}\n')
        self.tree.reboot(B4,unsynced='keep')
        self.assertEqual(self.decision(self.boot())['decision'],'candidate')

    def test_radio_retry_fail_closed_matrix(self):
        cases={'foreign-release':lambda:self.radio_faulted(release='f'*24),
               'claim-of-another-boot':lambda:self.radio_faulted(claim_boot=B5),
               'same-boot':lambda:self.radio_faulted(boot=B2),
               'no-resident':lambda:(self.radio_faulted(),(self.cfg/'native-select.resident').unlink()),
               'health-fault':lambda:(self.radio_faulted(),(self.cfg/'runtime-health-fault').write_text('x\n')),
               'extra-state':lambda:(self.radio_faulted(),(self.tree.state_dir/'inflight').write_text('x\n')),
               'no-claim':lambda:(self.radio_faulted(),(self.tree.state_dir/('claimed-'+ATT)).unlink())}
        for name,setup in cases.items():
            with self.subTest(case=name):
                shutil.rmtree(self.base/'tree');self.tree=self.make_tree()
                setup()
                self.assert_stock_unchanged('radio-fault')

    def test_power_cut_after_the_ledger_never_retries_twice(self):
        self.radio_faulted()
        (self.cfg/'native-select.radio-used').write_text(
            f'format=cammy-radio-retry-used/1\nrelease_id={RELEASE}\ncandidate_md5={md5(CAND)}\nfailed_boot={B1}\nretry_boot={B2}\n')
        self.assert_stock_unchanged('radio-retry-spent')

    def test_observe_mode_never_spends_the_radio_retry(self):
        shutil.rmtree(self.base/'tree');self.tree=self.make_tree(mode='mode=observe\n')
        self.radio_faulted()
        result=self.boot();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.decision(result)['decision'],'stock')
        self.assertFalse((self.cfg/'native-select.radio-used').exists())
        self.assertTrue((self.cfg/'startup.pending').exists())

    def test_crash_retry_spent_before_any_native_launch(self):
        self.crashed_resident()
        self.assertEqual(self.decision(self.boot())['decision'], 'candidate')
        self.assertTrue((self.cfg/'native-select.crash-used').is_file())
        self.assertFalse((self.cfg/'startup.pending').exists())
        self.assertFalse((self.cfg/'runtime-health-fault').exists())
        # Ledger alone blocks even if a normal health probe retired the claim.
        (self.tree.state_dir / ('claimed-' + ATT)).unlink()
        self.tree.reboot(B3, unsynced='keep')
        self.assert_stock_unchanged('crash-retry-spent')

    def test_crash_power_cut_never_launches_on_a_boot_other_than_the_spent_boot(self):
        for cut in range(1, 14):
            for outcome in ('keep', 'lose', 'truncate'):
                with self.subTest(cut=cut, unsynced=outcome):
                    self.reset()
                    self.crashed_resident()
                    self.tree.set_faults(f'powerloss-at-sync-{cut}')
                    result = self.boot()
                    ledger = self.cfg/'native-select.crash-used'
                    history = self.cfg/'native-release-history'
                    # The generic boot model persists only the claim directory.
                    # Model the old-or-new JFFS2 outcome for the new NOR records.
                    if result.returncode and cut == 1:
                        if outcome == 'lose':
                            ledger.unlink()
                        elif outcome == 'truncate':
                            ledger.write_bytes(ledger.read_bytes()[:30])
                    if result.returncode and outcome == 'lose':
                        archive_name = {2: 'runtime-health-fault', 3: 'startup.pending'}.get(cut)
                        if archive_name:
                            (history / f'{archive_name}.crash-{B1}').rename(self.cfg / archive_name)
                    self.tree.reboot(B3, unsynced=outcome)
                    if ledger.exists():
                        self.assert_stock_unchanged('crash-retry-spent')
                    else:
                        # An uncommitted spend admitted no launch on B2.
                        self.assertNotIn('candidate-stub', self.tree.launched_entries())
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(self.decision(self.boot())['decision'], 'candidate')
                        self.tree.reboot(B4, unsynced='keep')
                        self.assert_stock_unchanged('crash-retry-spent')

    def test_full_history_evictions_are_synced_before_either_retry_spends(self):
        for policy in ('power', 'crash'):
            for cut in range(1, 7):
                with self.subTest(policy=policy, sync=cut):
                    self.reset()
                    (self.interrupted if policy == 'power' else self.crashed_resident)()
                    hist = self.cfg / 'native-release-history'
                    hist.mkdir()
                    old = []
                    for n in range(4):
                        entry = hist / f'claimed-{n:032x}.healthy-{B4}'
                        entry.write_text('prior health evidence\n')
                        os.utime(entry, (n + 10, n + 10))
                        old.append(entry)
                    ledger = self.cfg / ('native-select.retry-used' if policy == 'power'
                                         else 'native-select.crash-used')
                    guard = self.cfg / 'startup.pending'
                    self.tree.set_faults(f'powerloss-at-sync-{cut}')
                    result = self.boot()
                    self.assertLessEqual(len(list(hist.iterdir())), 4)
                    self.assertFalse(old[0].exists())
                    self.assertEqual(old[1].exists(), cut == 1)
                    self.assertTrue(all(path.exists() for path in old[2:]))
                    self.assertEqual(ledger.exists(), cut >= 3)
                    # The two new sync points affect evidence only. A cut before
                    # the spend leaves an eligible live guard on the next boot.
                    if cut <= 2:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertTrue(guard.exists())
                        self.assertNotIn('candidate-stub', self.tree.launched_entries())
                        self.tree.reboot(B3, unsynced='keep')
                        self.assertEqual(self.decision(self.boot())['decision'], 'candidate')
                        self.assertEqual(len(list(hist.iterdir())), 4)
                    elif policy == 'crash':
                        self.tree.reboot(B3, unsynced='keep')
                        self.assert_stock_unchanged('crash-retry-spent')

    # ---- deterministic power loss at every durable transition ----
    def test_power_loss_at_each_retry_sync_never_allows_a_second_retry(self):
        for n in range(1,20):
            with self.subTest(sync=n):
                self.reset();self.interrupted()
                guard=(self.cfg/'startup.pending').read_bytes();claim=(self.tree.state_dir/('claimed-'+ATT)).read_bytes()
                self.tree.set_faults(f'powerloss-at-sync-{n}')
                result=self.boot()
                crashed=result.returncode!=0
                self.tree.set_faults()
                if not crashed:
                    self.assertEqual(self.decision(result)['decision'],'candidate')
                    break
                hist=self.cfg/'native-release-history'
                ledger=self.cfg/'native-select.retry-used'
                # Evidence survives: in place or archived, byte for byte, never both.
                live_guard=self.cfg/'startup.pending';old_guard=hist/f'startup.pending.retry-{B1}'
                live_claim=self.tree.state_dir/('claimed-'+ATT);old_claim=hist/f'claimed-{ATT}.retry-{B1}'
                archived=old_guard.exists()
                if archived:
                    self.assertEqual(old_guard.read_bytes(),guard);self.assertFalse(live_guard.exists())
                    self.assertEqual(old_claim.read_bytes(),claim);self.assertTrue(ledger.exists())
                else:
                    self.assertEqual(live_guard.read_bytes(),guard)
                    held=[q.read_bytes() for q in [live_claim,old_claim] if q.exists()]
                    self.assertEqual(held,[claim])
                self.tree.reboot(B3,unsynced='keep')
                spent=ledger.exists()
                if spent and not archived:
                    self.assert_stock_unchanged('retry-consumed');continue
                r3=self.boot();self.assertEqual(r3.returncode,0,r3.stderr);d3=self.decision(r3)
                if not spent:
                    # Lost before the ledger existed: nothing was spent, this is the first retry.
                    self.assertEqual(d3['decision'],'candidate')
                elif d3['decision']!='candidate':
                    # Lost inside the normal selection after the retry: its own refusals apply.
                    self.assertIn(d3['reason'],{'prior-inflight','post-attempt-boot'});continue
                # Whatever boot ran the retry is interrupted in late startup: never a second retry.
                live_guard.write_text(f'phase=idle_ready\nboot_id={B3}\n')
                self.tree.reboot(B4,unsynced='keep')
                self.assertTrue(ledger.exists())
                self.assert_stock_unchanged('retry-consumed')

        # Ledger, claim archive and flag archive each had a crash point, then selection's own.
        self.assertGreaterEqual(n,4)

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
