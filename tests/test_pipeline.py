"""Vérifie la chaîne de données avec des images synthétiques, sans webcam."""
import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import main

main.cv2 = cv2
main.np = np


class PipelineTests(unittest.TestCase):
    def test_train_reload_and_identify(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rng = np.random.default_rng(42)
            samples = []
            for name in ("Eljah", "Aminata"):
                folder = root / "faces" / name
                folder.mkdir(parents=True)
                face = rng.integers(0, 256, (200, 200), dtype=np.uint8)
                samples.append((name, face))
                for index in range(10):
                    self.assertTrue(cv2.imwrite(str(folder / f"{index}.png"), face))
            main.train(argparse.Namespace(data=root))
            model = cv2.face.LBPHFaceRecognizer_create()
            model.read(str(root / "model.yml"))
            for name, face in samples:
                identity, distance = model.predict(main.prepare(face))
                self.assertEqual(model.getLabelInfo(identity), name)
                self.assertLess(distance, 1)

    def test_empty_data_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "Aucun visage"):
                main.train(argparse.Namespace(data=Path(directory)))

    def test_single_capture_trains_reloads_and_identifies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "faces" / "Eljah"
            folder.mkdir(parents=True)
            face = np.random.default_rng(7).integers(50, 200, (200, 200), dtype=np.uint8)
            self.assertTrue(cv2.imwrite(str(folder / "one.png"), face))
            main.train(argparse.Namespace(data=root))
            model = cv2.face.LBPHFaceRecognizer_create()
            model.read(str(root / "model.yml"))
            identity, distance = model.predict(main.prepare(face))
            self.assertEqual(model.getLabelInfo(identity), "Eljah")
            self.assertLess(distance, 1)

    def test_cli_defaults_to_one_capture(self):
        with patch.object(main.sys, "argv", ["main.py", "enregistrer", "--nom", "Test"]), patch.object(main, "webcam") as webcam:
            self.assertEqual(main.main(), 0)
        self.assertEqual(webcam.call_args.args[0].samples, 1)

    def test_cli_capture_count_bounds(self):
        for value in ("1", "500"):
            self.assertEqual(main.sample_count(value), int(value))
        for value in ("0", "-1", "501"):
            with self.assertRaises(argparse.ArgumentTypeError):
                main.sample_count(value)

    def test_path_traversal_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            main.person_name("../ailleurs")


if __name__ == "__main__":
    unittest.main()
