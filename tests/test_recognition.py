import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np

from recognition import Confirmation, quality, load_settings, save_settings, validate_settings
import worker


class RecognitionTests(unittest.TestCase):
    def test_confirmation_resets_on_unknown_movement_and_identity_change(self):
        gate = Confirmation(3)
        box = (0, 0, 100, 100)
        self.assertFalse(gate.update("A", box))
        self.assertFalse(gate.update("A", box))
        self.assertTrue(gate.update("A", box))
        self.assertFalse(gate.update("", box))
        self.assertFalse(gate.update("A", box))
        self.assertFalse(gate.update("A", (400, 0, 100, 100)))
        self.assertEqual(gate.count, 1)
        self.assertFalse(gate.update("B", (400, 0, 100, 100)))
        self.assertEqual(gate.count, 1)

    def test_quality_rejects_dark_bright_and_blurred(self):
        for brightness in (0, 120, 255):
            self.assertFalse(quality(np.full((100, 100), brightness, np.uint8))[0])
        image = np.random.default_rng(1).integers(50, 200, (100, 100), dtype=np.uint8)
        self.assertTrue(quality(image)[0])

    def test_settings_round_trip_and_invalid_input(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            self.assertEqual(load_settings(data)["samples"], 1)
            values = save_settings(data, {"camera": 2, "threshold": 52})
            self.assertEqual(load_settings(data), values)
            self.assertEqual(values["samples"], 1)
            self.assertEqual(validate_settings({"samples": 500})["samples"], 500)
            for values in ({"camera": -1}, {"threshold": "nan"}, {"samples": 0}, {"samples": 501}, {"samples": 1.5}, {"confirmations": 2.5}):
                with self.assertRaises(ValueError):
                    validate_settings(values)
            (data / "settings.json").write_text("{broken")
            with self.assertRaises(ValueError):
                load_settings(data)

    def test_camera_released_on_read_error(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(settings=json.dumps({}), mode="apercu", data=Path(directory), preview=Path(directory))
            with patch.object(worker.engine, "detector"), patch.object(worker.cv2, "VideoCapture") as factory:
                camera = factory.return_value
                camera.isOpened.return_value = True
                camera.read.return_value = (False, None)
                with self.assertRaisesRegex(RuntimeError, "plus d'image"):
                    worker.run(args)
                camera.release.assert_called_once()

    def test_capture_skips_bad_frame_and_stops_after_one_without_stop_request(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            preview = data / "preview"
            preview.mkdir()
            frame = np.random.default_rng(3).integers(50, 200, (200, 200, 3), dtype=np.uint8)
            args = SimpleNamespace(settings=json.dumps({}), mode="enregistrer", name="Test", data=data, preview=preview)
            events = []
            def emit(**event):
                events.append(event)
            with patch.object(worker.engine, "detector") as detector, patch.object(worker.cv2, "VideoCapture") as factory, patch.object(worker, "emit", side_effect=emit), patch.object(worker.time, "monotonic", return_value=100):
                detector.return_value.detectMultiScale.return_value = [(20, 20, 150, 150)]
                factory.return_value.isOpened.return_value = True
                factory.return_value.read.side_effect = [(True, np.zeros_like(frame)), (True, frame)]
                worker.run(args)
                self.assertEqual(factory.return_value.read.call_count, 2)
                factory.return_value.release.assert_called_once()
            self.assertEqual(len(list((data / "faces" / "Test").glob("*.png"))), 1)
            self.assertTrue((preview / "frame.png").exists())
            self.assertEqual(events[-1]["count"], 1)
            self.assertEqual(events[-1]["status"], "Terminé · 1 capture enregistrée")
            self.assertFalse((preview / "stop").exists())

    def test_multiple_faces_never_captured(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            preview = data / "preview"
            preview.mkdir()
            args = SimpleNamespace(settings=json.dumps({}), mode="enregistrer", name="Test", data=data, preview=preview)
            with patch.object(worker.engine, "detector") as detector, patch.object(worker.cv2, "VideoCapture") as factory, patch.object(worker, "emit", side_effect=lambda **kw: (preview / "stop").touch()):
                detector.return_value.detectMultiScale.return_value = [(0, 0, 100, 100), (100, 0, 100, 100)]
                factory.return_value.isOpened.return_value = True
                factory.return_value.read.return_value = (True, np.zeros((200, 200, 3), np.uint8))
                worker.run(args)
            self.assertEqual(list((data / "faces" / "Test").glob("*.png")), [])
