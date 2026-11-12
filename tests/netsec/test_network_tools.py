"""Offline contracts for operator preparation and validation artifacts."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ToolsTests(unittest.TestCase):
    def test_component_plan_is_complete_and_does_not_build(self):
        root = Path(sys.argv[1])
        result = subprocess.run([sys.executable, str(root / 'scripts/verify-network.py'),
                                 '--component', 'dns', '--component', 'firewall', '--plan'],
                                capture_output=True, text=True, check=True)
        plan = json.loads(result.stdout)
        self.assertIn('proxy-regressions', plan['checks'])
        self.assertIn('proxy-network-vm', plan['checks'])
        self.assertEqual(plan['checks'], sorted(set(plan['checks'])))

    def test_benchmark_percentiles_and_sample_count(self):
        root = Path(sys.argv[1])
        spec = importlib.util.spec_from_file_location('benchmark', root / 'scripts/network-benchmark.py')
        benchmark = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(benchmark)
        result = benchmark.distribution([.001, .002, .003, .004, .005])
        self.assertEqual(result, {'samples':5, 'p50_ms':3, 'p95_ms':5})


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
