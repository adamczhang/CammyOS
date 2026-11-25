"""Worker, callback and buffer-lifetime tests; synthetic driver, no devices."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCENARIOS=('natural_completion','blocked_open','blocked_send','blocked_close',
           'close_failure','missing_frame_return','stale_duplicate_callbacks',
           'mailbox_overflow','callback_validation','send_no_effect','send_uncertain',
           'open_partial_failure','concurrent_producers','native_disabled','next_job_binding',
           'cancel_before_worker')


class AsyncAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        command=os.environ.get('CAMMY_ASYNC_FIXTURE')
        if command:
            cls.command=json.loads(command)
        else:
            subprocess.run(['make','-s','-C','native/audio','async-host'],cwd=ROOT,
                           check=True,capture_output=True,timeout=30)
            cls.command=[str(ROOT/'build/native-audio/host/async-fixture')]

    def scenario(self,number):
        env=dict(os.environ)
        if env.get('CAMMY_ASYNC_TSAN')=='1' and number in (4,5,7):
            # Only these three intentionally pin a process-lifetime worker.
            # Race detection remains enabled, including in these cases.
            env['TSAN_OPTIONS']=env.get('TSAN_OPTIONS','')+' report_thread_leaks=0'
        result=subprocess.run([*self.command,str(number)],cwd=ROOT,capture_output=True,
                              text=True,timeout=15,env=env)
        self.assertEqual(result.returncode,0,result.stderr)
        record=json.loads(result.stdout)
        self.assertEqual(record['scenario'],number)
        self.assertTrue(record['synthetic'])
        self.assertFalse(record['physical_available'])


def _case(number):
    def test(self):
        self.scenario(number)
    return test


for _number,_name in enumerate(SCENARIOS):
    setattr(AsyncAudioTests,'test_'+_name,_case(_number))
