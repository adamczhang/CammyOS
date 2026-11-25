"""No hardware: verify the narrow boot/native identity override and syntax."""
import subprocess
import unittest
from tools import face_trial_media as M


class TrialMediaTests(unittest.TestCase):
    def test_only_native_identity_contexts_change_and_guards_are_added(self):
        boot = '9c66a2ed-a2df-4b88-998c-d31abbf82771'
        blob = M.build('a' * 64, boot).decode()
        self.assertEqual(blob.count('owned "$NPID" "$NTICK" ' + M.CANDIDATE_PATH), 2)
        self.assertIn('sha256sum /usr/bin/sdvcam', blob)
        self.assertIn('sha256sum ' + M.CANDIDATE_PATH, blob)
        self.assertIn(boot, blob)
        before = M.SOURCE.read_text()
        # Aside from the explicit added guard, exactly two identity checks differ.
        guard_start = blob.index('# Trial scope:')
        guard_end = blob.index('note(){', guard_start)
        restored = blob[:guard_start] + blob[guard_end:]
        restored = restored.replace('owned "$NPID" "$NTICK" ' + M.CANDIDATE_PATH,
                                    'owned "$NPID" "$NTICK" /usr/bin/sdvcam')
        self.assertEqual(restored, before)
        subprocess.run(['dash', '-n'], input=blob.encode(), check=True)

    def test_invalid_identity_is_rejected_before_generation(self):
        for digest, boot in [('x' * 64, 'bad'), ('a' * 64, 'bad'), ('a;reboot', 'bad')]:
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                M.build(digest, boot)
