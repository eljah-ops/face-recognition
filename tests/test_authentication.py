"""Invariants d'authentification ; images synthétiques et PAD contrôlé en tests."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from authentication import FaceAuthenticator, verification_path
from gui import archive_person
from liveness import DEFAULT_MODEL, PadDecision, PassiveLiveness, verified_weights
from qa.backends import FaceStudioAdapter
import worker


class TestPad:
    simulated = True

    def __init__(self):
        self.live = True
        self.calls = 0

    def predict(self, frame):
        self.calls += 1
        return PadDecision(self.live, 0.99 if self.live else 0.01)


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.root = Path(self.storage.name)
        self.pad = TestPad()
        self.factory = patch("authentication.PassiveLiveness", return_value=self.pad)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.service = FaceAuthenticator(self.root, {"threshold": 1, "confirmations": 2})
        self.service.cascade = Mock()
        self.service.cascade.detectMultiScale.return_value = [(20, 20, 120, 120)]
        rng = np.random.default_rng(8)
        self.a, self.b = (rng.integers(50, 200, (180, 180, 3), dtype=np.uint8) for _ in range(2))

    def test_real_lbph_enrollment_reload_and_strict_one_to_one(self):
        self.assertFalse(self.service.authenticate("A", [self.a]).accepted)
        self.service.enroll("A", [self.a])
        self.service.enroll("B", [self.b])
        a = self.service.authenticate("A", [self.a, self.a])
        self.assertTrue(a.accepted)
        self.assertEqual(a.matched_identity, "A")
        self.assertTrue(a.liveness_passed)
        self.assertFalse(self.service.authenticate("A", [self.b, self.b]).accepted)
        self.assertTrue(self.service.authenticate("B", [self.b, self.b]).accepted)
        self.assertFalse(self.service.authenticate("Unknown", [self.a, self.a]).accepted)
        # Chaque fichier ne contient que le label 0 de sa propre identité.
        model = cv2.face.LBPHFaceRecognizer_create()
        model.read(str(verification_path(self.root, "A")))
        self.assertEqual(set(model.getLabels().reshape(-1)), {0})
        self.assertEqual(model.getLabelInfo(0), "A")

    def test_photo_and_video_rejected_when_pad_rejects_a_matching_face(self):
        self.service.enroll("A", [self.a])
        self.pad.live = False
        for frames in ([self.a], [self.a] * 8):
            with self.subTest(frames=len(frames)):
                decision = self.service.authenticate("A", frames)
                self.assertFalse(decision.accepted)
                self.assertIs(decision.liveness_passed, False)

    def test_enrollment_rejects_spoof_without_persisting_a_profile(self):
        self.pad.live = False
        with self.assertRaises(ValueError):
            self.service.enroll("A", [self.a])
        self.assertFalse(verification_path(self.root, "A").exists())
        self.assertFalse((self.root / "faces").exists())

    def test_quality_failure_resets_temporal_confirmation(self):
        self.service.enroll("A", [self.a])
        dark = np.zeros_like(self.a)
        result = self.service.authenticate("A", [self.a, dark, self.a])
        self.assertFalse(result.accepted)
        result = self.service.authenticate("A", [self.a, dark, self.a, self.a])
        self.assertTrue(result.accepted)
        result = self.service.authenticate("A", [dark])
        self.assertFalse(result.accepted)
        self.assertIsNone(result.liveness_passed)

    def test_multiple_faces_and_partial_crop_never_grant_access(self):
        self.service.enroll("A", [self.a])
        for boxes in ([], [(10, 10, 110, 110), (30, 30, 110, 110)], [(0, 0, 120, 120)]):
            self.service.cascade.detectMultiScale.return_value = boxes
            self.assertFalse(self.service.authenticate("A", [self.a, self.a]).accepted)

    def test_no_pad_no_grant_and_no_enrollment(self):
        self.service.enroll("A", [self.a])
        self.service.pad = None
        with self.assertRaises(RuntimeError):
            self.service.authenticate("A", [self.a, self.a])
        with self.assertRaises(RuntimeError):
            self.service.enroll("B", [self.b])

    def test_bad_inference_values_are_errors_not_successful_rejections(self):
        self.service.enroll("A", [self.a])
        session = self.service.session("A")
        for distance in (float("nan"), float("inf"), -1):
            session.model = Mock()
            session.model.predict.return_value = (0, distance)
            with self.assertRaises(RuntimeError):
                session.step(self.a)
        self.pad.predict = Mock(side_effect=RuntimeError("PAD failure"))
        with self.assertRaises(RuntimeError):
            self.service.authenticate("A", [self.a])

    def test_replaced_identity_model_is_rejected(self):
        self.service.enroll("A", [self.a])
        self.service.enroll("B", [self.b])
        verification_path(self.root, "A").write_bytes(verification_path(self.root, "B").read_bytes())
        with self.assertRaisesRegex(RuntimeError, "identité revendiquée"):
            self.service.session("A")

    def test_duplicate_enrollment_preserves_existing_profile(self):
        self.service.enroll("A", [self.a])
        path = verification_path(self.root, "A")
        original = path.read_bytes()
        with self.assertRaises(ValueError):
            self.service.enroll("A", [self.b])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(list((self.root / "faces" / "A").glob("*.png"))), 1)

    def test_archive_revokes_even_an_existing_session(self):
        self.service.enroll("A", [self.a])
        session = self.service.session("A")
        session.step(self.a)
        archive = archive_person(self.root, "A")
        self.assertTrue((archive / "verification.yml").is_file())
        self.assertFalse(verification_path(self.root, "A").exists())
        self.assertFalse(session.step(self.a).accepted)

    def test_internal_symlink_and_concurrent_enrollment_are_rejected(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            (self.root / "verification").symlink_to(elsewhere, target_is_directory=True)
            with self.assertRaises(RuntimeError):
                self.service.enroll("A", [self.a])
            self.assertEqual(list(Path(elsewhere).iterdir()), [])
            (self.root / "verification").unlink()
        (self.root / ".authentication.lock").mkdir()
        with self.assertRaisesRegex(RuntimeError, "déjà en cours"):
            self.service.enroll("A", [self.a])

    def test_integrated_qa_adapter_uses_real_storage_and_cleans_it(self):
        adapter = FaceStudioAdapter(settings={"threshold": 1, "confirmations": 2})
        self.addCleanup(adapter.close)
        adapter.engine.cascade = self.service.cascade
        self.assertTrue(adapter.supports_one_to_one)
        self.assertTrue(adapter.supports_liveness)
        self.assertFalse(adapter.authenticate("A", [self.a]).accepted)
        adapter.enroll("A", [self.a])
        self.assertTrue(adapter.authenticate("A", [self.a, self.a]).accepted)
        directory = Path(adapter.storage.name)
        self.assertTrue(directory.is_dir())
        adapter.close()
        self.assertFalse(directory.exists())

    def test_camera_worker_uses_same_decisions_and_releases_camera(self):
        self.service.enroll("A", [self.a])
        preview = self.root / "preview"
        preview.mkdir()
        args = SimpleNamespace(mode="authentifier", data=self.root, name="A", preview=preview,
                               settings=json.dumps({"threshold": 1, "confirmations": 2}))
        for live in (True, False):
            self.pad.live = live
            events = []
            with patch("authentication.FaceAuthenticator", return_value=self.service), \
                    patch.object(worker.cv2, "VideoCapture") as factory, \
                    patch.object(worker, "emit", side_effect=lambda **event: events.append(event)):
                camera = factory.return_value
                camera.isOpened.return_value = True
                camera.read.side_effect = [(True, self.a), (True, self.a)]
                self.assertEqual(worker.run_authentication(args), live)
                camera.release.assert_called_once()
            self.assertEqual(any(event.get("confirmed") for event in events), live)

    def test_missing_pad_blocks_before_opening_camera(self):
        self.service.pad = None
        args = SimpleNamespace(mode="inscrire", data=self.root, name="A", preview=None, settings="{}")
        with patch("authentication.FaceAuthenticator", return_value=self.service), patch.object(worker.cv2, "VideoCapture") as factory:
            with self.assertRaises(RuntimeError):
                worker.run_authentication(args)
            factory.assert_not_called()


class LivenessTests(unittest.TestCase):
    def test_rgb_normalization_and_real_class_index(self):
        pad = PassiveLiveness.__new__(PassiveLiveness)
        pad.threshold, pad.net = 0.95, Mock()
        pad.net.forward.return_value = np.array([[0.96, 0.04]], dtype=np.float32)
        decision = pad.predict(np.full((64, 64, 3), (10, 20, 30), dtype=np.uint8))
        self.assertTrue(decision.is_live)
        blob = pad.net.setInput.call_args.args[0]
        self.assertEqual(blob.shape, (1, 3, 128, 128))
        np.testing.assert_allclose(blob[0, :, 0, 0], [(30 - 151.2405) / 63.0105,
                                                    (20 - 119.595) / 56.457,
                                                    (10 - 107.8395) / 55.0035], rtol=1e-6)
        pad.net.forward.return_value = np.array([[0.04, 0.96]], dtype=np.float32)
        self.assertFalse(pad.predict(np.ones((64, 64, 3), dtype=np.uint8)).is_live)

    def test_invalid_pad_probabilities_cannot_grant_access(self):
        pad = PassiveLiveness.__new__(PassiveLiveness)
        pad.threshold, pad.net = 0.95, Mock()
        for output in ([float("nan"), 0], [1, 1], [-0.1, 1.1], [1], [2, -1]):
            pad.net.forward.return_value = np.array(output)
            with self.assertRaises(RuntimeError):
                pad.predict(np.zeros((64, 64, 3), np.uint8))

    def test_integrity_verification_rejects_modified_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.onnx"
            path.write_bytes(b"abc")
            with patch("liveness.MODEL_SIZE", 3), patch("liveness.MODEL_SHA384", hashlib.sha384(b"abc").hexdigest()):
                self.assertEqual(verified_weights(path), b"abc")
                path.write_bytes(b"xyz")
                with self.assertRaisesRegex(RuntimeError, "Empreinte"):
                    verified_weights(path)

    @unittest.skipUnless(DEFAULT_MODEL.is_file(), "Poids PAD officiels non installés dans cet environnement")
    def test_official_model_loads_and_runs_on_synthetic_input(self):
        result = PassiveLiveness().predict(np.full((128, 128, 3), 128, np.uint8))
        self.assertTrue(np.isfinite(result.probability))
        self.assertGreaterEqual(result.probability, 0)
        self.assertLessEqual(result.probability, 1)
        # Ce test vérifie le câblage du modèle, pas son efficacité biométrique.
