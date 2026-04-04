import subprocess
import tempfile
import unittest
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_experiment_matrix.py"


class RunExperimentMatrixTests(unittest.TestCase):
    def _run_with_config(self, cfg: dict) -> list[str]:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as tmp:
            tmp.write(json.dumps(cfg))
            cfg_path = Path(tmp.name)

        try:
            proc = subprocess.run(
                ["python3", str(SCRIPT), str(cfg_path)],
                check=True,
                capture_output=True,
                text=True,
            )
        finally:
            cfg_path.unlink(missing_ok=True)

        return [line for line in proc.stdout.splitlines() if line.strip()]

    def test_matrix_row_contains_resource_columns(self) -> None:
        cfg = {
            "global": {"iterations": 1, "requests_per_client": 100, "p99_threshold_ms": 9.5},
            "matrix": {
                "noise_level": ["low"],
                "traffic_pattern": ["constant"],
                "isolation_method": ["none"],
                "identity_mode": ["ip"],
                "container_count": [3],
                "failure_mode": ["none"],
                "tenant_cpu_quota_pct": [75],
                "tenant_memory_mb": [768],
                "tenant_cpuset": ["1-2"],
                "noisy_cpu_quota_pct": [35],
                "noisy_memory_mb": [256],
                "noisy_cpuset": ["3"],
                "host_reserved_cpus": ["0"],
                "host_mem_pressure_mb": [1024],
                "io_read_bps": [2097152],
                "io_write_bps": [1048576],
            },
        }

        rows = self._run_with_config(cfg)
        self.assertEqual(len(rows), 1)

        parts = rows[0].split("|")
        self.assertEqual(len(parts), 22)
        self.assertEqual(parts[12], "75")
        self.assertEqual(parts[13], "768")
        self.assertEqual(parts[14], "1-2")
        self.assertEqual(parts[15], "35")
        self.assertEqual(parts[16], "256")
        self.assertEqual(parts[17], "3")
        self.assertEqual(parts[18], "0")
        self.assertEqual(parts[19], "1024")
        self.assertEqual(parts[20], "2097152")
        self.assertEqual(parts[21], "1048576")

    def test_scenario_defaults_fill_resource_columns(self) -> None:
        cfg = {
            "scenarios": [
                {
                    "name": "smoke_case",
                    "noise_level": "medium",
                    "traffic_pattern": "bursty",
                    "isolation_method": "adaptive",
                    "identity_mode": "cgroup",
                    "container_count": 3,
                    "requests": 100,
                }
            ]
        }

        rows = self._run_with_config(cfg)
        self.assertEqual(len(rows), 1)

        parts = rows[0].split("|")
        self.assertEqual(len(parts), 22)
        self.assertEqual(parts[12], "80")
        self.assertEqual(parts[13], "512")
        self.assertEqual(parts[14], "auto")
        self.assertEqual(parts[15], "60")
        self.assertEqual(parts[16], "384")
        self.assertEqual(parts[17], "auto")
        self.assertEqual(parts[19], "0")
        self.assertEqual(parts[20], "0")
        self.assertEqual(parts[21], "0")


if __name__ == "__main__":
    unittest.main()
