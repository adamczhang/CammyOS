"""PC voice chain for Cammy's speaker (host/cammy_voicefx.py)."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
import cammy_voicefx as fx  # noqa: E402


def tone(freqs, seconds=2.0, rate=24000, amp=12000):
    t = np.arange(int(seconds * rate)) / rate
    return (sum(np.sin(2 * np.pi * f * t) for f in freqs) / len(freqs) * amp).astype('<i2').tobytes()


def band_power(pcm, f0, rate=16000):
    y = np.frombuffer(pcm, '<i2').astype(float)
    spectrum = np.abs(np.fft.rfft(y)) ** 2
    freqs = np.fft.rfftfreq(len(y), 1 / rate)
    return spectrum[np.abs(freqs - f0) < 10].sum()


class VoiceFxTests(unittest.TestCase):
    def test_levels_respect_camera_cap(self):
        for level, peak in fx.LEVELS.items():
            out = np.frombuffer(fx.speaker_pcm(tone([440, 1000]), level), '<i2')
            self.assertLessEqual(np.abs(out).max(), peak)
            self.assertGreater(np.abs(out).max(), peak * 0.9)
            self.assertLess(peak, 10000)

    def test_output_is_16k_and_fits_one_job(self):
        out = fx.speaker_pcm(tone([440], seconds=4.0))
        seconds = len(out) / 2 / 16000
        self.assertTrue(3.9 < seconds < 4.5, seconds)

    def test_bass_is_cut_and_voice_band_kept(self):
        out = fx.speaker_pcm(tone([100, 1000]))
        self.assertLess(band_power(out, 100), band_power(out, 1000) / 1000)

    def test_silence_is_rejected(self):
        with self.assertRaises(ValueError):
            fx.speaker_pcm(bytes(48000))


if __name__ == '__main__':
    unittest.main()
