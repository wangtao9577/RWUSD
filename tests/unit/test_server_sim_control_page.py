from pathlib import Path
import unittest


CONTROL_PAGE = Path(__file__).resolve().parents[2] / "server_sim_control" / "index.html"


class ServerSimControlPageTests(unittest.TestCase):
    def test_control_page_contains_simulation_only_and_ssh_forwarding_copy(self) -> None:
        page = CONTROL_PAGE.read_text(encoding="utf-8")

        self.assertIn("Project2 Simulation Credential Entry", page)
        self.assertIn("simulation-only", page)
        self.assertIn("SSH port forwarding", page)
        self.assertIn("does not modify the public 8081 monitor page", page)
        self.assertIn("does not touch the three running production accounts", page)

    def test_control_page_points_to_local_control_server_endpoints(self) -> None:
        page = CONTROL_PAGE.read_text(encoding="utf-8")

        self.assertIn('fetch("/api/config-status"', page)
        self.assertIn('fetch("/api/config"', page)
        self.assertIn('name="api_key"', page)
        self.assertIn('name="api_secret"', page)


if __name__ == "__main__":
    unittest.main()
