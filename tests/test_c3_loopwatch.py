"""Loop watch: a blocked event loop is logged with the blocking stack."""
import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from c3.loopwatch import LoopWatch  # noqa: E402


def block_the_loop():
    time.sleep(0.5)


class LoopWatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocking_call_is_named_and_timed(self):
        watch = LoopWatch(stall_s=0.25)
        task = asyncio.create_task(watch.run())
        await asyncio.sleep(0.2)
        with self.assertLogs('c3.loopwatch', 'WARNING') as logs:
            block_the_loop()
            await asyncio.sleep(0.3)
        task.cancel()
        self.assertEqual(watch.stalls, 1)
        self.assertGreaterEqual(watch.longest_s, 0.4)
        self.assertIn('block_the_loop', watch.last['where'])
        self.assertIn('blocked', logs.output[0])


if __name__ == '__main__':
    unittest.main()
