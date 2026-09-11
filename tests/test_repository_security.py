"""Contrôle réel de l'index Git avec uniquement des données factices."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.check_repository import check_index, content_problem, path_problem


class RepositorySecurityTests(unittest.TestCase):
    def test_private_paths_and_unexpected_files_rejected(self):
        for name in (
            "data/model.yml", "custom/faces/Test/one.png", "custom/archives/model.yml",
            "custom/model-backup.yml", "custom/settings.json", "session.csv",
            ".env.production", "cert.pem", "capture.PNG", "backup.zip",
            "secrets.toml", "credentials.json", ".gitmodules",
            "reports/biometric-results.md", "qa_datasets/identities.txt",
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(path_problem(name))
        for name in ("main.py", "README.md", ".github/workflows/ci.yml", "requirements.txt"):
            self.assertIsNone(path_problem(name))

    def test_renamed_models_binaries_and_tokens_rejected(self):
        fake_token = b"ghp_" + b"x" * 36
        for content in (
            fake_token, b"-----BEGIN " + b"RSA PRIVATE KEY-----",
            b"%YAML:1.0\nopencv_" + b"lbphfaces:\n  radius: 1",
            b"\x89PNG\r\n", b"text\0binary",
        ):
            self.assertIsNotNone(content_problem(content))
        self.assertIsNone(content_problem('print("bonjour")\n'.encode()))

    def test_index_checked_even_when_working_file_is_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.git(root, "init", "-q")
            path = root / "config.py"
            path.write_text("TOKEN = '" + "ghp_" + "x" * 36 + "'\n")
            self.git(root, "add", "config.py")
            path.write_text("# le secret a été retiré uniquement du fichier local\n")
            count, issues = check_index(root)
            self.assertEqual(count, 1)
            self.assertIn("secret potentiel", issues[0][1])
            self.git(root, "add", "config.py")
            self.assertEqual(check_index(root), (1, []))

    def test_force_added_data_rejected_and_gitignore_covers_custom_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.git(root, "init", "-q")
            rules = Path(__file__).resolve().parents[1] / ".gitignore"
            (root / ".gitignore").write_bytes(rules.read_bytes())
            for name in ("custom/faces/Test/capture.png", "custom/model.yml", "session.csv", ".env.local",
                         "reports/report.json", "qa_datasets/manifest.json"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("factice")
                self.git(root, "check-ignore", "--quiet", name)
            self.git(root, "add", ".gitignore")
            self.assertEqual(check_index(root), (1, []))
            self.git(root, "add", "--force", "custom/model.yml")
            self.assertEqual(len(check_index(root)[1]), 1)

    def test_symbolic_link_and_empty_index_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.git(root, "init", "-q")
            self.assertTrue(check_index(root)[1])
            # Un objet Git de mode symlink fonctionne aussi sur Windows sans
            # avoir à créer un lien symbolique sur le système de fichiers.
            target = root / "target"
            target.write_text("/outside/private")
            sha = self.git(root, "hash-object", "-w", "target").stdout.decode().strip()
            self.git(root, "update-index", "--add", "--cacheinfo", "120000", sha, "link.py")
            self.assertIn("lien symbolique", check_index(root)[1][0][1])

    @staticmethod
    def git(root, *args):
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
