import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "analyse_reponses.py"
SPEC = importlib.util.spec_from_file_location("analyse_reponses", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class AnalyseReponsesTests(unittest.TestCase):
    def test_config_ne_lit_que_les_valeurs_attendues(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rediger.env"
            path.write_text("APP_USER='demo'\nAPP_PASSWORD=secret-demo\nORIGIN_URL=https://exemple.test/\nOTHER=ignored\n")
            self.assertEqual(module.read_config(path), ("https://exemple.test", "demo", "secret-demo"))

    def test_rendu_signale_petits_echantillons_sans_donnee_personnelle(self):
        output = module.render({"total": 12, "opens": None, "tables": {"activity": [
            {"name": "Boulangerie", "contacted": 4, "replied": 1, "interested": 1}],
            "angle": [], "score": [], "channel": []}})
        self.assertIn("Échantillon trop petit", output)
        self.assertIn("4 | 1 | 25 % | 1 | 25 %", output)
        self.assertIn("Ouvertures : indisponibles", output)
        self.assertNotIn("secret-demo", output)
        self.assertNotIn("@", output)

    def test_seuil_total_49_vs_50(self):
        data = {"total": 49, "tables": {}}
        self.assertIn("moins de 50", module.render(data))
        data["total"] = 50
        self.assertNotIn("moins de 50", module.render(data))

    def test_groupes_de_9_exclus_et_groupes_de_10_comparables(self):
        left = {"name": "Groupe A", "contacted": 10, "replied": 4, "interested": 2}
        right = {"name": "Groupe B", "contacted": 10, "replied": 1, "interested": 0}
        data = {"total": 50, "tables": {"activity": [dict(left, contacted=9), right]}}
        self.assertEqual(module.recommendations(data), [])
        data["tables"]["activity"][0]["contacted"] = 10
        tips = module.recommendations(data)
        self.assertEqual(len(tips), 1)
        self.assertIn("4/10, 40 %", tips[0])
        self.assertIn("1/10, 10 %", tips[0])
        self.assertIn("Tester davantage le groupe Groupe A", tips[0])
        self.assertIn("pas preuve de causalité", tips[0])

    def test_taux_interesses_affiche(self):
        output = module.render({"total": 50, "tables": {"activity": [
            {"name": "Boulangerie", "contacted": 10, "replied": 4, "interested": 2}]}})
        self.assertIn("20 %", output)


if __name__ == "__main__":
    unittest.main()
