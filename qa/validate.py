"""Exécution séquentielle, assertions strictes et rapports JSON/JUnit.

python -m qa.validate --demo
python -m qa.validate --dataset /chemin/prive/manifest.json --adapter module:Factory
"""
import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET

from qa.backends import Decision, DemoBackend, FaceStudioAdapter
from qa.datasets import (
    DatasetError, SCENARIOS, load_demo_dataset, load_manifest, validate_dataset, validate_frames,
)


@dataclass(frozen=True)
class Policy:
    max_far: float = 0.001
    max_frr: float = 0.05
    max_mean_latency_ms: float = 100.0
    min_genuine_attempts: int = 100
    min_impostor_attempts: int = 1000
    min_attempts_per_scenario: int = 20
    require_liveness: bool = True
    require_one_to_one: bool = True
    max_spoof_acceptances: int = 0

    def __post_init__(self):
        for value in (self.max_far, self.max_frr, self.max_mean_latency_ms):
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Les seuils doivent être des nombres finis positifs ou nuls.")
        if max(self.max_far, self.max_frr) > 1 or self.max_mean_latency_ms == 0:
            raise ValueError("Taux dans [0, 1] et latence strictement positive requis.")
        for value in (self.min_genuine_attempts, self.min_impostor_attempts, self.min_attempts_per_scenario):
            if type(value) is not int or value < 1:
                raise ValueError("Les tailles minimales doivent être des entiers positifs.")
        if (self.require_liveness is not True or self.require_one_to_one is not True
                or type(self.max_spoof_acceptances) is not int or self.max_spoof_acceptances != 0):
            raise ValueError("PAD et 1:1 obligatoires ; aucune attaque acceptée n'est autorisée.")


@dataclass
class Result:
    case_id: str
    scenario: str
    kind: str
    status: str
    messages: list[str]
    latency_ms: float | None = None
    accepted: bool | None = None
    liveness_passed: bool | None = None
    identity_matches_claim: bool | None = None


def assess_decision(trial, decision):
    """Assertions explicites : restent actives avec python -O."""
    if not isinstance(decision, Decision) or type(decision.accepted) is not bool:
        raise TypeError("Le moteur doit retourner une Decision avec un booléen strict.")
    if decision.liveness_passed is not None and type(decision.liveness_passed) is not bool:
        raise TypeError("Le résultat PAD doit être True, False ou None.")
    if decision.matched_identity is not None and not isinstance(decision.matched_identity, str):
        raise TypeError("L'identité retournée doit être une chaîne ou None.")
    problems = []
    if decision.accepted != trial.expected_accept:
        problems.append("Acceptation attendue." if trial.expected_accept else "Refus obligatoire : accès accepté.")
    if decision.accepted and decision.matched_identity != trial.claimed_identity:
        problems.append("Violation du matching 1:1 : identité retournée différente de l'identité revendiquée.")
    if decision.accepted and decision.liveness_passed is not True:
        problems.append("Accès accepté sans validation explicite de la détection du vivant.")
    if trial.kind == "attack" and decision.liveness_passed is not False:
        problems.append("L'attaque doit être explicitement rejetée par le PAD ; un refus générique ne suffit pas.")
    return problems


def compute_metrics(results):
    """Une tentative = un flux complet ; aucune pondération par image vidéo."""
    trials = [r for r in results if r.kind in {"genuine", "impostor", "attack", "robustness"}]
    evaluated = [r for r in trials if r.accepted is not None]
    genuine = [r for r in evaluated if r.kind == "genuine"]
    impostors = [r for r in evaluated if r.kind == "impostor"]
    attacks = [r for r in evaluated if r.kind == "attack"]
    latencies = [r.latency_ms for r in evaluated if r.latency_ms is not None]

    def rate(numerator, denominator):
        return numerator / denominator if denominator else None

    false_accepts = sum(r.accepted for r in impostors)
    # Une mauvaise identité retournée n'est pas une authentification réussie.
    false_rejects = sum(not r.accepted or r.identity_matches_claim is False for r in genuine)
    spoof_accepts = sum(r.accepted for r in attacks)
    return {
        "attempts": len(trials), "evaluated_attempts": len(evaluated),
        "error_attempts": sum(r.status == "error" for r in trials),
        "genuine_attempts": len(genuine), "impostor_attempts": len(impostors),
        "attack_attempts": len(attacks), "false_acceptances": false_accepts,
        "false_rejections": false_rejects, "spoof_acceptances": spoof_accepts,
        "FAR": rate(false_accepts, len(impostors)),
        "FRR": rate(false_rejects, len(genuine)),
        "spoof_acceptance_rate": rate(spoof_accepts, len(attacks)),
        "mean_inference_latency_ms": statistics.fmean(latencies) if latencies else None,
        "latency_samples": len(latencies),
        "by_scenario": {name: {
            "evaluated": sum(r.scenario == name for r in evaluated),
            "accepted": sum(r.accepted for r in evaluated if r.scenario == name),
            "errors": sum(r.scenario == name and r.status == "error" for r in trials),
        } for name in SCENARIOS},
    }


def _fingerprints(frames):
    return tuple(hashlib.sha256(str(frame.shape).encode() + frame.tobytes()).digest() for frame in frames)


def make_report(results, policy, simulated, backend_name):
    metrics = compute_metrics(results)
    failed = sum(r.status == "failed" for r in results)
    errors = sum(r.status == "error" for r in results)
    passed = bool(results) and not failed and not errors and not simulated
    return {
        "schema_version": 1, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "backend": backend_name,
        "measurement_kind": "simulation" if simulated else "application",
        "release_gate_passed": passed,
        "status": "passed" if passed else "blocked",
        "policy": asdict(policy),
        "metric_scope": {
            "FAR": "Acceptations / tentatives d'imposteurs ordinaires évaluées ; attaques PAD séparées.",
            "FRR": "Rejets ou mauvaises identités / essais légitimes nominaux et changements d'apparence évalués.",
            "robustness": "Conditions extrêmes hors conditions d'usage : refus exigé, exclus du FRR nominal.",
            "latency": "Appel authenticate complet : prétraitement, PAD et matching ; chargement des médias et enrôlement exclus.",
            "missing_data": "null signifie non mesuré ; les erreurs ne sont jamais des refus sûrs et bloquent la validation.",
        },
        "metrics": metrics,
        "summary": {"tests": len(results), "failures": failed, "errors": errors},
        "results": [asdict(r) for r in results],
    }


def run_suite(dataset, backend, policy):
    results = []
    simulated = dataset.simulated or getattr(backend, "simulated", True) is not False

    def gate(name, ok, message):
        results.append(Result("gate-" + name, "gate", "gate", "passed" if ok else "failed",
                              [] if ok else [message]))

    gate("real-data", not simulated, "Une simulation ne peut pas autoriser un déploiement.")
    gate("liveness", getattr(backend, "supports_liveness", False) is True,
         "Détection du vivant absente ou non déclarée par l'adaptateur.")
    gate("one-to-one", getattr(backend, "supports_one_to_one", False) is True,
         "Le moteur doit vérifier l'identité revendiquée en 1:1, pas identifier en 1:N.")
    try:
        validate_dataset(dataset)
        enrollment_hashes, enrollment_by_identity = set(), {}
        # Vérifier la séparation enrôlement/probes avant toute mesure. Ne garder
        # que les empreintes en mémoire et ne jamais les écrire dans le rapport.
        for identity, loader in dataset.enrollments.items():
            hashes = _fingerprints(validate_frames(loader()))
            enrollment_by_identity[identity] = hashes
            enrollment_hashes.update(hashes)
        seen_probes = set()
        enrolled = set()

        def execute(trial):
            try:
                if trial.scenario != "unknown_claim" and trial.claimed_identity not in enrolled:
                    raise DatasetError("Enrôlement préalable non réussi.")
                frames = validate_frames(trial.load())
                if trial.scenario == "smartphone_video" and len(frames) < 2:
                    raise DatasetError("Une attaque vidéo doit fournir plusieurs images.")
                hashes = _fingerprints(frames)
                key = (trial.claimed_identity, trial.kind, hashes)
                if not simulated:
                    if trial.kind != "attack" and enrollment_hashes.intersection(hashes):
                        raise DatasetError("Fuite de données : un probe réutilise une image d'enrôlement.")
                    if key in seen_probes:
                        raise DatasetError("Tentative dupliquée : elle gonflerait artificiellement les effectifs.")
                seen_probes.add(key)
                started = time.perf_counter_ns()
                decision = backend.authenticate(trial.claimed_identity, frames)
                latency = (time.perf_counter_ns() - started) / 1_000_000
                problems = assess_decision(trial, decision)
                results.append(Result(trial.trial_id, trial.scenario, trial.kind,
                                      "failed" if problems else "passed", problems,
                                      latency, decision.accepted, decision.liveness_passed,
                                      decision.matched_identity == trial.claimed_identity))
            except Exception as error:
                # Ne pas recopier une exception externe pouvant contenir noms,
                # chemins, secrets ou réponses d'API dans les journaux.
                message = str(error) if isinstance(error, DatasetError) else type(error).__name__
                results.append(Result(trial.trial_id, trial.scenario, trial.kind, "error", [message]))

        for identity, loader in dataset.enrollments.items():
            try:
                probe = next(t for t in dataset.trials if t.scenario == "nominal" and t.claimed_identity == identity)
                # Une identité nouvelle ne doit pas fonctionner avant inscription.
                # Cette précondition n'entre pas dans les métriques d'inférence.
                before = backend.authenticate(identity, validate_frames(probe.load()))
                problems = assess_decision(replace(probe, scenario="unknown_claim"), before)
                results.append(Result("before-enroll-" + identity, "pre_enrollment", "setup",
                                      "failed" if problems else "passed", problems))
                frames = validate_frames(loader())
                if _fingerprints(frames) != enrollment_by_identity[identity]:
                    raise DatasetError("Les données ont changé pendant la campagne.")
                backend.enroll(identity, frames)
                enrolled.add(identity)
                results.append(Result("enroll-" + identity, "enrollment", "setup", "passed", []))
            except Exception as error:
                results.append(Result("enroll-" + identity, "enrollment", "setup", "error", [type(error).__name__]))
            # Matching immédiat avant d'enrôler la personne suivante. On utilise
            # une nouvelle capture, jamais les images d'entraînement en mode réel.
            for trial in dataset.trials:
                if trial.scenario == "nominal" and trial.claimed_identity == identity:
                    execute(trial)
        for trial in dataset.trials:
            if trial.scenario != "nominal":
                execute(trial)
    except Exception as error:
        message = str(error) if isinstance(error, DatasetError) else type(error).__name__
        results.append(Result("dataset", "setup", "setup", "error", [message]))
    finally:
        try:
            backend.close()
        except Exception as error:
            results.append(Result("cleanup", "setup", "setup", "error", [type(error).__name__]))

    metrics = compute_metrics(results)
    gate("genuine-size", metrics["genuine_attempts"] >= policy.min_genuine_attempts,
         "Nombre d'essais légitimes évalués insuffisant.")
    gate("impostor-size", metrics["impostor_attempts"] >= policy.min_impostor_attempts,
         "Nombre d'essais d'imposteurs évalués insuffisant.")
    for scenario, counts in metrics["by_scenario"].items():
        gate("coverage-" + scenario, counts["evaluated"] >= policy.min_attempts_per_scenario,
             "Scénario absent ou effectif évalué insuffisant : " + scenario)
    for metric, limit in (("FAR", policy.max_far), ("FRR", policy.max_frr),
                          ("mean_inference_latency_ms", policy.max_mean_latency_ms)):
        value = metrics[metric]
        gate(metric, value is not None and value <= limit, f"{metric} non mesuré ou supérieur au seuil {limit}.")
    gate("zero-spoof", metrics["attack_attempts"] > 0 and metrics["spoof_acceptances"] == 0,
         "Une attaque a été acceptée ou aucune attaque n'a pu être évaluée.")
    report = make_report(results, policy, simulated, type(backend).__name__)
    if isinstance(backend, FaceStudioAdapter):
        report["backend_metadata"] = backend.audit_metadata
    return report


def _atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_reports(report, output):
    output = Path(output)
    _atomic_text(output / "report.json", json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    suite = ET.Element("testsuite", name="face-recognition-release-gate",
                       tests=str(report["summary"]["tests"]), failures=str(report["summary"]["failures"]),
                       errors=str(report["summary"]["errors"]), skipped="0")
    properties = ET.SubElement(suite, "properties")
    for key, value in {"measurement_kind": report["measurement_kind"],
                       "release_gate_passed": str(report["release_gate_passed"]).lower(),
                       **{k: report["metrics"][k] for k in ("FAR", "FRR", "mean_inference_latency_ms")}}.items():
        ET.SubElement(properties, "property", name=key, value="not_measured" if value is None else str(value))
    for result in report["results"]:
        case = ET.SubElement(suite, "testcase", name=result["case_id"], classname=result["scenario"],
                             time=str((result["latency_ms"] or 0) / 1000))
        if result["status"] != "passed":
            node = ET.SubElement(case, "error" if result["status"] == "error" else "failure",
                                 message="; ".join(result["messages"]))
            node.text = "\n".join(result["messages"])
    _atomic_text(output / "junit.xml", ET.tostring(suite, encoding="unicode", xml_declaration=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="Fixtures fictives ; ne peut jamais valider un déploiement")
    mode.add_argument("--dataset", type=Path, help="Manifeste local avec médias de test consentis")
    parser.add_argument("--adapter", help="Factory Python de confiance sous la forme module:Factory")
    parser.add_argument("--pad-model", type=Path, help="Poids officiels anti-spoof-mn3 pour l'adaptateur intégré")
    parser.add_argument("--policy", type=Path, default=Path(__file__).with_name("policy.toml"))
    parser.add_argument("--output", type=Path, default=Path("reports/face-qa"))
    args = parser.parse_args(argv)
    policy = Policy()
    try:
        policy = Policy(**tomllib.loads(args.policy.read_text(encoding="utf-8")))
        if (args.demo or args.adapter) and args.pad_model:
            raise ValueError("--pad-model est réservé à l'adaptateur applicatif intégré.")
        if args.demo and args.adapter:
            raise ValueError("--demo et --adapter sont incompatibles.")
        if not args.demo and args.dataset is None:
            raise DatasetError("Fournir --dataset ou --demo ; aucune donnée de production n'est chargée implicitement.")
        dataset = load_demo_dataset() if args.demo else load_manifest(args.dataset)
        if args.adapter:
            module, factory = args.adapter.split(":", 1)
            backend = getattr(importlib.import_module(module), factory)()
        else:
            backend = DemoBackend() if args.demo else FaceStudioAdapter(**({"pad_model": args.pad_model} if args.pad_model else {}))
        report = run_suite(dataset, backend, policy)
    except Exception as error:
        # Un défaut de configuration ou de chargement produit aussi les rapports.
        message = str(error) if isinstance(error, DatasetError) else type(error).__name__
        report = make_report([Result("setup", "setup", "setup", "error", [message])],
                             policy, bool(args.demo), "not_initialized")
    try:
        write_reports(report, args.output)
    except OSError:
        print("Impossible d'écrire les rapports dans le dossier demandé.", file=sys.stderr)
        return 2
    metrics = report["metrics"]
    print(f"Mode : {report['measurement_kind']} | Validation : {report['status']}")
    for key in ("FAR", "FRR", "spoof_acceptance_rate", "mean_inference_latency_ms"):
        value = metrics[key]
        print(f"{key} : " + ("non mesuré" if value is None else f"{value:.6f}"))
    print(f"Rapports JSON et JUnit : {args.output}")
    if report["measurement_kind"] == "simulation" or report["summary"]["errors"]:
        return 2
    return 0 if report["release_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
