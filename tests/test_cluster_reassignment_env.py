import importlib
import os
import unittest
from unittest.mock import patch

from app.pipeline import runner


def _valor(texto):
    """Valor de PARASITE_CLUSTER_REASSIGNMENT al importar runner con esa variable."""
    env = dict(os.environ)
    env.pop("PARASITE_CLUSTER_REASSIGNMENT", None)
    if texto is not None:
        env["PARASITE_CLUSTER_REASSIGNMENT"] = texto
    with patch.dict(os.environ, env, clear=True):
        importlib.reload(runner)
        return runner.PARASITE_CLUSTER_REASSIGNMENT


class ClusterReassignmentEnvTests(unittest.TestCase):
    def tearDown(self):
        importlib.reload(runner)

    def test_por_defecto_queda_prendida(self):
        self.assertIs(_valor(None), True)

    def test_valores_que_la_apagan(self):
        for texto in ("0", "false", "False", "no", "off", " OFF "):
            self.assertIs(_valor(texto), False, texto)

    def test_valores_que_la_prenden(self):
        for texto in ("1", "true", "yes", "si"):
            self.assertIs(_valor(texto), True, texto)


if __name__ == "__main__":
    unittest.main()
