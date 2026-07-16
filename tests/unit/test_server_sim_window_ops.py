from pathlib import Path
import unittest


class ServerSimWindowOpsTests(unittest.TestCase):
    def test_run_project2_sim_forever_uses_project_venv_python(self) -> None:
        script_path = Path("D:/project2/ops/server_sim_window/run_project2_sim_forever.sh")
        source = script_path.read_text(encoding="utf-8")

        self.assertIn("./.venv/bin/python scripts/run_server_sim_daemon.py", source)

    def test_run_project2_sim_forever_enforces_single_instance_lock(self) -> None:
        script_path = Path("D:/project2/ops/server_sim_window/run_project2_sim_forever.sh")
        source = script_path.read_text(encoding="utf-8")

        self.assertIn("rwusd_sim_daemon.lock", source)
        self.assertIn("flock -n", source)
        self.assertIn("already running", source)


if __name__ == "__main__":
    unittest.main()
