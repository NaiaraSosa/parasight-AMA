import importlib
import os
import unittest
from unittest.mock import patch

from app.pipeline import runner


def _value(text):
    """Value of PARASITE_CLUSTER_REASSIGNMENT after importing runner with that variable."""
    env = dict(os.environ)
    env.pop("PARASITE_CLUSTER_REASSIGNMENT", None)
    if text is not None:
        env["PARASITE_CLUSTER_REASSIGNMENT"] = text
    with patch.dict(os.environ, env, clear=True):
        importlib.reload(runner)
        return runner.PARASITE_CLUSTER_REASSIGNMENT


class ClusterReassignmentEnvTests(unittest.TestCase):
    def tearDown(self):
        importlib.reload(runner)

    def test_on_by_default(self):
        self.assertIs(_value(None), True)

    def test_values_that_turn_it_off(self):
        for text in ("0", "false", "False", "no", "off", " OFF "):
            self.assertIs(_value(text), False, text)

    def test_values_that_turn_it_on(self):
        for text in ("1", "true", "yes", "si"):
            self.assertIs(_value(text), True, text)


if __name__ == "__main__":
    unittest.main()
