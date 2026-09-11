"""Teste le banc QA avec des doublures, sans prétendre mesurer un visage réel."""
from collections import deque
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import cv2
import numpy as np

from qa.backends import Decision, DemoBackend, FaceStudioAdapter
from qa.datasets import (
    DatasetError, Trial, _media_loader, load_demo_dataset, load_manifest,
    validate_dataset, validate_frames,
)
from qa.validate import Policy, Result, assess_decision, compute_metrics, main, run_suite, write_reports


class ScriptedBackend:
    """Seulement pour les tests unitaires du validateur ; jamais pour une campagne."""
    simulated = False
    supports_liveness = True
    supports_one_to_one = True

    def __init__(self, dataset):
        nominal = []
        for identity in dataset.enrollments:
            probes = [t for t in dataset.trials if t.scenario == "nominal" and t.claimed_identity == identity]
            nominal += [replace(probes[0], scenario="unknown_claim"), *probes]
        remaining = [t for t in dataset.trials if t.scenario != "nominal"]
        self.decisions = deque(Decision(t.expected_accept, t.claimed_identity if t.expected_accept else None,
                                       False if t.kind == "attack" else True if t.expected_accept else None)
                               for t in nominal + remaining)
        self.events = []
        self.closed = False

    def enroll(self, identity, frames):
        self.events.append(("enroll", identity))

    def authenticate(self, claimed_identity, frames):
        self.events.append(("authenticate", claimed_identity))
        return self.decisions.popleft()

    def close(self):
        self.closed = True


def isolated_dataset():
    # L'indicateur False sert UNIQUEMENT à tester les barrières du validateur.
    # Chaque probe est différent pour tester le contrôle de fuite de données.
    dataset = load_demo_dataset()
    rng = np.random.default_rng(7)
    trials = []
    for trial in dataset.trials:
        frame = rng.integers(0, 256, (32, 32, 3), dtype=np.uint8)
        count = 3 if trial.scenario == "smartphone_video" else 1
        trials.append(replace(trial, load=lambda frame=frame, count=count: tuple(frame.copy() for _ in range(count))))
    return replace(dataset, trials=trials, simulated=False)


def small_policy(**changes):
    values = dict(min_genuine_attempts=1, min_impostor_attempts=1,
                  min_attempts_per_scenario=1, max_mean_latency_ms=10_000)
    values.update(changes)
    return replace(Policy(), **values)


class QAMetricsTests(unittest.TestCase):
    def test_far_frr_and_latency_have_independent_denominators(self):
        results = [
            Result("i1", "impostor_unknown", "impostor", "failed", [], 10, True),
            Result("i2", "impostor_unknown", "impostor", "passed", [], 20, False),
            Result("g1", "nominal", "genuine", "passed", [], 30, True, True, True),
            Result("g2", "nominal", "genuine", "failed", [], 40, False),
            Result("s1", "smartphone_video", "attack", "failed", [], 50, True),
            Result("r1", "low_light", "robustness", "passed", [], 60, False),
            Result("e1", "nominal", "genuine", "error", ["RuntimeError"]),
        ]
        metrics = compute_metrics(results)
        self.assertEqual(metrics["FAR"], 0.5)
        self.assertEqual(metrics["FRR"], 0.5)
        self.assertEqual(metrics["spoof_acceptance_rate"], 1.0)
        self.assertEqual(metrics["mean_inference_latency_ms"], 35)
        self.assertEqual(metrics["attack_attempts"], 1)
        self.assertEqual(metrics["error_attempts"], 1)
        self.assertEqual(metrics["attempts"], 7)
        self.assertEqual(metrics["evaluated_attempts"], 6)

    def test_unmeasured_rates_are_null_and_wrong_identity_is_not_success(self):
        self.assertIsNone(compute_metrics([])["FAR"])
        self.assertIsNone(compute_metrics([])["FRR"])
        self.assertIsNone(compute_metrics([])["mean_inference_latency_ms"])
        result = Result("wrong", "nominal", "genuine", "failed", [], 1, True, True, False)
        self.assertEqual(compute_metrics([result])["FRR"], 1)

    def test_security_assertions_reject_spoof_and_missing_pad(self):
        trial = load_demo_dataset().trials[5]  # Attaque photo.
        self.assertEqual(trial.scenario, "printed_photo_hd")
        self.assertTrue(assess_decision(trial, Decision(True, "person_a", False)))
        self.assertTrue(assess_decision(trial, Decision(False, None, None)))
        self.assertEqual(assess_decision(trial, Decision(False, None, False)), [])
        nominal = replace(trial, scenario="nominal")
        self.assertTrue(assess_decision(nominal, Decision(True, "person_b", True)))
        self.assertTrue(assess_decision(nominal, Decision(True, "person_a", None)))
        with self.assertRaises(TypeError):
            assess_decision(trial, Decision("false", None, False))

    def test_policy_cannot_disable_security_or_accept_nonfinite_thresholds(self):
        for changes in ({"require_liveness": False}, {"require_one_to_one": False},
                        {"max_spoof_acceptances": 1}, {"max_far": float("nan")},
                        {"max_frr": 1.1}, {"max_mean_latency_ms": float("inf")},
                        {"min_genuine_attempts": 0}, {"min_impostor_attempts": 1.5}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Policy(**changes)


class QARunnerTests(unittest.TestCase):
    def test_enrollment_immediately_followed_by_matching_and_complete_reports(self):
        dataset = isolated_dataset()
        backend = ScriptedBackend(dataset)
        with patch("qa.validate.time.perf_counter_ns", side_effect=range(0, 100_000_000, 1_000_000)):
            report = run_suite(dataset, backend, small_policy())
        self.assertTrue(report["release_gate_passed"])
        self.assertTrue(backend.closed)
        self.assertEqual(backend.events[:6], [("authenticate", "person_a"), ("enroll", "person_a"),
                                             ("authenticate", "person_a"), ("authenticate", "person_b"),
                                             ("enroll", "person_b"), ("authenticate", "person_b")])
        self.assertEqual(report["metrics"]["mean_inference_latency_ms"], 1.0)
        self.assertEqual(report["metrics"]["attack_attempts"], 2)
        with tempfile.TemporaryDirectory() as directory:
            write_reports(report, directory)
            saved = json.loads((Path(directory) / "report.json").read_text())
            suite = ET.parse(Path(directory) / "junit.xml").getroot()
            self.assertEqual(saved, report)
            self.assertEqual(int(suite.attrib["tests"]), len(suite.findall("testcase")))
            self.assertEqual(int(suite.attrib["failures"]), 0)
            self.assertEqual(int(suite.attrib["errors"]), 0)

    def test_demo_never_validates_release_and_exposes_spoof_acceptances(self):
        report = run_suite(load_demo_dataset(), DemoBackend(), small_policy())
        self.assertFalse(report["release_gate_passed"])
        self.assertEqual(report["measurement_kind"], "simulation")
        self.assertEqual(report["metrics"]["spoof_acceptances"], 2)
        self.assertEqual(report["metrics"]["attack_attempts"], 2)

    def test_acceptance_before_enrollment_blocks_an_otherwise_correct_run(self):
        dataset = isolated_dataset()
        backend = ScriptedBackend(dataset)
        backend.decisions[0] = Decision(True, "person_a", True)
        report = run_suite(dataset, backend, small_policy())
        self.assertFalse(report["release_gate_passed"])
        case = next(r for r in report["results"] if r["case_id"] == "before-enroll-person_a")
        self.assertEqual(case["status"], "failed")
        self.assertEqual(report["metrics"]["FAR"], 0)
        self.assertEqual(report["metrics"]["FRR"], 0)

    def test_latency_above_budget_blocks_an_accurate_backend(self):
        dataset = isolated_dataset()
        with patch("qa.validate.time.perf_counter_ns", side_effect=range(0, 1_000_000_000, 3_000_000)):
            report = run_suite(dataset, ScriptedBackend(dataset), small_policy(max_mean_latency_ms=2))
        self.assertFalse(report["release_gate_passed"])
        self.assertEqual(report["metrics"]["mean_inference_latency_ms"], 3)
        case = next(r for r in report["results"] if r["case_id"] == "gate-mean_inference_latency_ms")
        self.assertEqual(case["status"], "failed")

    def test_missing_pad_model_still_blocks_the_integrated_adapter(self):
        report = run_suite(isolated_dataset(), FaceStudioAdapter(pad_model="/nonexistent/anti-spoof-mn3.onnx"), small_policy())
        self.assertFalse(report["release_gate_passed"])
        failures = {r["case_id"] for r in report["results"] if r["status"] == "failed"}
        self.assertIn("gate-liveness", failures)
        self.assertNotIn("gate-one-to-one", failures)
        self.assertEqual(report["metrics"]["genuine_attempts"], 0)

    def test_missing_scenario_and_insufficient_population_block_release(self):
        dataset = isolated_dataset()
        dataset.trials = [t for t in dataset.trials if t.scenario != "beard"]
        report = run_suite(dataset, ScriptedBackend(dataset), Policy())
        failures = {r["case_id"] for r in report["results"] if r["status"] == "failed"}
        self.assertIn("gate-coverage-beard", failures)
        self.assertIn("gate-genuine-size", failures)
        self.assertIn("gate-impostor-size", failures)
        self.assertFalse(report["release_gate_passed"])

    def test_enrollment_leak_and_duplicate_probe_cannot_inflate_metrics(self):
        dataset = isolated_dataset()
        dataset.trials[0] = replace(dataset.trials[0], load=dataset.enrollments["person_a"])
        duplicate = replace(dataset.trials[2], trial_id="duplicate")
        dataset.trials.append(duplicate)
        report = run_suite(dataset, ScriptedBackend(dataset), small_policy())
        errors = {r["case_id"]: r for r in report["results"] if r["status"] == "error"}
        self.assertIn("nominal-a", errors)
        self.assertIn("duplicate", errors)
        self.assertFalse(report["release_gate_passed"])

    def test_engine_exception_is_not_a_safe_reject_and_logs_do_not_leak(self):
        dataset = isolated_dataset()
        backend = ScriptedBackend(dataset)
        with patch.object(backend, "authenticate", side_effect=RuntimeError("private/path credential-content")):
            report = run_suite(dataset, backend, small_policy())
        self.assertFalse(report["release_gate_passed"])
        self.assertIsNone(report["metrics"]["FAR"])
        self.assertGreater(report["metrics"]["error_attempts"], 0)
        self.assertNotIn("credential-content", json.dumps(report))
        self.assertTrue(backend.closed)
        with tempfile.TemporaryDirectory() as directory:
            write_reports(report, directory)
            suite = ET.parse(Path(directory) / "junit.xml").getroot()
            self.assertEqual(len(suite.findall("testcase/error")), report["summary"]["errors"])
            self.assertEqual(len(suite.findall("testcase/failure")), report["summary"]["failures"])

    def test_cli_writes_reports_for_demo_and_missing_data(self):
        for mode in (["--demo"], []):
            with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
                self.assertEqual(main([*mode, "--output", directory]), 2)
                report = json.loads((Path(directory) / "report.json").read_text())
                self.assertFalse(report["release_gate_passed"])
                self.assertTrue((Path(directory) / "junit.xml").is_file())


class QADatasetTests(unittest.TestCase):
    def test_ground_truth_and_claim_must_form_a_real_impostor_pair(self):
        dataset = load_demo_dataset()
        dataset.trials[0] = replace(dataset.trials[0], scenario="impostor_registered")
        with self.assertRaises(DatasetError):
            validate_dataset(dataset)

    def test_image_manifest_is_lazy_and_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(cv2.imwrite(str(root / "enroll.png"), np.full((32, 32, 3), 100, np.uint8)))
            self.assertTrue(cv2.imwrite(str(root / "probe.png"), np.full((32, 32, 3), 101, np.uint8)))
            spec = {"schema_version": 1, "simulated": False,
                    "enrollments": {p: {"frames": ["enroll.png"]} for p in ("a", "b")},
                    "trials": [{"id": p, "scenario": "nominal", "claimed_identity": p,
                                "subject_identity": p, "media": {"frames": ["probe.png"]}} for p in ("a", "b")]}
            (root / "manifest.json").write_text(json.dumps(spec))
            dataset = load_manifest(root / "manifest.json")
            self.assertEqual(dataset.trials[0].load()[0].shape, (32, 32, 3))
            with self.assertRaises(DatasetError):
                _media_loader(root, {"frames": ["../outside.png"]})
            (root / "probe.png").unlink()
            with self.assertRaises(DatasetError):
                dataset.trials[0].load()

    def test_video_releases_camera_and_refuses_silent_truncation(self):
        frame = np.zeros((32, 32, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory, patch("qa.datasets.cv2.VideoCapture") as factory:
            camera = factory.return_value
            camera.isOpened.return_value = True
            camera.read.side_effect = [(True, frame), (True, frame), (False, None)]
            loader = _media_loader(Path(directory), {"video": "recording.mp4"})
            self.assertEqual(len(loader()), 2)
            camera.release.assert_called_once()
            camera.reset_mock()
            camera.read.side_effect = [(True, frame)] * 3
            with patch("qa.datasets.MAX_FRAMES", 2), self.assertRaises(DatasetError):
                loader()
            camera.release.assert_called_once()

    def test_bad_image_format_or_empty_stream_is_rejected(self):
        for frames in ((), (np.zeros((32, 32), np.uint8),), (np.zeros((32, 32, 3), float),)):
            with self.assertRaises(DatasetError):
                validate_frames(frames)


if __name__ == "__main__":
    unittest.main()
