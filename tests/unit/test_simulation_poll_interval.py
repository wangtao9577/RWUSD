import unittest

from src.app.bootstrap import simulation_poll_interval_seconds


class SimulationPollIntervalTests(unittest.TestCase):
    def test_uses_primary_bar_interval_as_real_time_poll_interval(self) -> None:
        self.assertEqual(simulation_poll_interval_seconds("5m"), 300.0)
        self.assertEqual(simulation_poll_interval_seconds("1h"), 3600.0)

    def test_rejects_unknown_bar_interval(self) -> None:
        with self.assertRaises(ValueError):
            simulation_poll_interval_seconds("7m")


if __name__ == "__main__":
    unittest.main()
