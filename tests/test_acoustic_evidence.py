import array
import math
import random
from pathlib import Path
import tempfile
import unittest
from tools.acoustic_evidence import RATE, analyze_samples, analyze_wav, cue_samples, write_cue, verify_routes


class AcousticEvidenceTests(unittest.TestCase):
    def test_known_sequence_survives_attenuation_delay_and_noise(self):
        rng = random.Random(9)
        samples = [0] * 2000 + list(cue_samples())
        samples = array.array('h', [round(x * .2) + rng.randint(-100, 100) for x in samples])
        result = analyze_samples(samples)
        self.assertTrue(result['detected_ordered_cue'])
        self.assertEqual([p['frequency_hz'] for p in result['tone_peaks']], [700, 1100, 1700])

    def test_silence_and_noise_do_not_establish_camera_hearing(self):
        rng = random.Random(10)
        for samples in [[0] * (3 * RATE), [rng.randint(-300, 300) for _ in range(3 * RATE)]]:
            self.assertFalse(analyze_samples(samples)['detected_ordered_cue'])

    def test_single_carrier_and_reversed_sequence_are_rejected(self):
        samples = [round(1000 * math.sin(2 * math.pi * 1100 * i / RATE)) for i in range(3 * RATE)]
        self.assertFalse(analyze_samples(samples)['detected_ordered_cue'])
        self.assertFalse(analyze_samples(list(reversed(cue_samples())))['detected_ordered_cue'])

    def test_wav_generation_and_analysis(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cue.wav'
            write_cue(path)
            self.assertTrue(analyze_wav(path)['detected_ordered_cue'])
        with self.assertRaises(ValueError):
            analyze_samples([0] * 100)

    def test_routes_bind_process_to_client_to_node_to_physical_endpoint(self):
        def item(kind, ident, props):
            return {'type': 'PipeWire:Interface:' + kind, 'id': ident, 'info': {'props': props}}
        routes = {'Audio/Sink': {'id': 34}, 'Audio/Source': {'id': 62}}
        graph = [item('Client', 79, {'application.process.id': 100}),
                 item('Client', 89, {'application.process.id': 101}),
                 item('Node', 69, {'client.id': 79}), item('Node', 93, {'client.id': 89}),
                 item('Link', 96, {'link.output.node': 62, 'link.input.node': 69}),
                 item('Link', 97, {'link.output.node': 93, 'link.input.node': 34})]
        result = verify_routes(graph, (100, 101), routes)
        self.assertTrue(result['p15_playback_route_verified'])
        self.assertTrue(result['p15_reference_route_verified'])
        other = verify_routes(graph, (999,), routes)
        self.assertFalse(other['p15_playback_route_verified'])
        self.assertFalse(other['p15_reference_route_verified'])
        monitor = verify_routes(graph, (100, 101), {'Audio/Sink': {'id': 34}, 'Audio/Source': {'id': 999}})
        self.assertFalse(monitor['p15_reference_route_verified'])
