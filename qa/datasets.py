"""Chargeurs remplaçables : fixtures synthétiques ou manifeste local consenti.

Les annotations ne sont jamais transmises au moteur. Les médias réels sont
chargés à la demande, une tentative à la fois, avant le chronométrage.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Callable

import cv2
import numpy as np

Frames = tuple[np.ndarray, ...]
Loader = Callable[[], Frames]

# Les cas hors des conditions d'usage exigent un refus sûr et restent séparés
# du FRR nominal. Ne pas changer les attentes pour faire réussir un moteur.
SCENARIOS = {
    "nominal": ("genuine", True),
    "glasses": ("genuine", True),
    "surgical_mask": ("genuine", True),
    "beard": ("genuine", True),
    "low_light": ("robustness", False),
    "backlight": ("robustness", False),
    "extreme_angle": ("robustness", False),
    "partial_face": ("robustness", False),
    "printed_photo_hd": ("attack", False),
    "smartphone_video": ("attack", False),
    "impostor_registered": ("impostor", False),
    "impostor_unknown": ("impostor", False),
    "unknown_claim": ("impostor", False),
}
MAX_FRAMES = 120
MAX_MEDIA_BYTES = 256 * 1024 * 1024


class DatasetError(ValueError):
    """Données absentes, incohérentes ou non exploitables."""


@dataclass(frozen=True)
class Trial:
    trial_id: str
    scenario: str
    claimed_identity: str
    subject_identity: str
    load: Loader

    @property
    def kind(self):
        return SCENARIOS[self.scenario][0]

    @property
    def expected_accept(self):
        return SCENARIOS[self.scenario][1]


@dataclass
class Dataset:
    enrollments: dict[str, Loader]
    trials: list[Trial]
    simulated: bool = False


def validate_dataset(dataset):
    def identifier(value):
        return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value)

    if type(dataset.simulated) is not bool or len(dataset.enrollments) < 2:
        raise DatasetError("Il faut au moins deux identités d'enrôlement distinctes.")
    if not all(identifier(key) for key in dataset.enrollments):
        raise DatasetError("Utiliser des pseudonymes alphanumériques de 1 à 64 caractères.")
    seen = set()
    for trial in dataset.trials:
        if not all(identifier(value) for value in (
            trial.trial_id, trial.claimed_identity, trial.subject_identity,
        )) or trial.trial_id in seen or trial.scenario not in SCENARIOS:
            raise DatasetError("Identifiant dupliqué, invalide ou scénario inconnu.")
        seen.add(trial.trial_id)
        known_claim = trial.claimed_identity in dataset.enrollments
        known_subject = trial.subject_identity in dataset.enrollments
        if known_claim == (trial.scenario == "unknown_claim"):
            raise DatasetError("Identité revendiquée incompatible avec le scénario.")
        if trial.kind == "impostor":
            if trial.subject_identity == trial.claimed_identity:
                raise DatasetError("Un imposteur doit revendiquer une autre identité.")
            if trial.scenario == "impostor_registered" and not known_subject:
                raise DatasetError("L'imposteur enregistré doit être enrôlé.")
            if trial.scenario == "impostor_unknown" and known_subject:
                raise DatasetError("L'imposteur inconnu ne doit pas être enrôlé.")
            if trial.scenario == "unknown_claim" and not known_subject:
                raise DatasetError("Le contrôle d'identité inconnue utilise un sujet déjà enrôlé.")
        elif trial.subject_identity != trial.claimed_identity:
            raise DatasetError("Sujet et identité revendiquée doivent correspondre.")
    for identity in dataset.enrollments:
        if not any(t.scenario == "nominal" and t.claimed_identity == identity for t in dataset.trials):
            raise DatasetError("Chaque enrôlement exige un essai nominal immédiat distinct.")


def validate_frames(frames):
    if not isinstance(frames, (tuple, list)) or not 1 <= len(frames) <= MAX_FRAMES:
        raise DatasetError("Une tentative doit contenir de 1 à 120 images.")
    total = 0
    for frame in frames:
        if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8:
            raise DatasetError("Images uint8 requises.")
        if frame.ndim != 3 or frame.shape[2] != 3 or min(frame.shape[:2]) < 16:
            raise DatasetError("Images BGR à trois canaux requises, au moins 16 × 16.")
        total += frame.nbytes
        if total > MAX_MEDIA_BYTES:
            raise DatasetError("Tentative trop volumineuse (maximum 256 Mio décodés).")
    return tuple(frames)


def _media_loader(root, spec):
    """Point d'intégration pour votre gestionnaire de datasets sécurisé."""
    if not isinstance(spec, dict) or ("frames" in spec) == ("video" in spec):
        raise DatasetError("Définir soit frames, soit video pour chaque média.")

    def local_path(value):
        if not isinstance(value, str) or Path(value).is_absolute():
            raise DatasetError("Chemin relatif au manifeste requis.")
        path = (root / value).resolve()
        if not path.is_relative_to(root):
            raise DatasetError("Un média sort du dossier du dataset.")
        return path

    if "frames" in spec:
        paths = spec["frames"]
        if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_FRAMES:
            raise DatasetError("Liste d'images vide ou trop longue.")
        paths = tuple(local_path(value) for value in paths)

        def load():
            frames = []
            for path in paths:
                if not path.is_file():
                    raise DatasetError("Image absente ou illisible.")
                frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if frame is None:
                    raise DatasetError("Image absente ou illisible.")
                frames.append(frame)
                validate_frames(frames)
            return tuple(frames)
        return load

    path = local_path(spec["video"])

    def load_video():
        camera = cv2.VideoCapture(str(path))
        try:
            if not camera.isOpened():
                raise DatasetError("Vidéo absente ou illisible.")
            frames = []
            while True:
                ok, frame = camera.read()
                if not ok:
                    break
                frames.append(frame)
                validate_frames(frames)  # Refuser une troncature silencieuse.
            if len(frames) < 2:
                raise DatasetError("Une vidéo de rejeu doit fournir au moins deux images.")
            return tuple(frames)
        finally:
            camera.release()
    return load_video


def load_manifest(path):
    path = Path(path).resolve()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1 or type(raw.get("simulated")) is not bool:
        raise DatasetError("schema_version=1 et simulated explicite sont requis.")
    dataset = Dataset(
        {identity: _media_loader(path.parent, spec) for identity, spec in raw["enrollments"].items()},
        [Trial(item["id"], item["scenario"], item["claimed_identity"], item["subject_identity"],
               _media_loader(path.parent, item["media"])) for item in raw["trials"]],
        raw["simulated"],
    )
    validate_dataset(dataset)
    return dataset


def _fixture(frame, count=1):
    return lambda: tuple(frame.copy() for _ in range(count))


def load_spoofing_dataset(face):
    """FICTIF : remplacer par des prises de vue de vrais supports d'attaque.

    Copier des pixels ne simule ni une imprimante HD ni un écran de smartphone.
    Ces fixtures vérifient uniquement le routage des attaques et les assertions.
    """
    return [
        Trial("attack-photo", "printed_photo_hd", "person_a", "person_a", _fixture(face)),
        Trial("attack-video", "smartphone_video", "person_a", "person_a", _fixture(face, 8)),
    ]


def load_environment_dataset(face):
    """FICTIF : transformations déterministes des limites de qualité."""
    low_light = np.clip(face.astype(float) * 0.08, 0, 255).astype(np.uint8)
    backlight = np.full_like(face, 255)
    backlight[24:72, 24:72] = low_light[24:72, 24:72]
    rotation = cv2.getRotationMatrix2D((48, 48), 75, 1)
    tilted = cv2.warpAffine(face, rotation, (96, 96))
    partial = face.copy()
    partial[:, :72] = 0
    return [Trial("env-" + name, name, "person_a", "person_a", _fixture(frame))
            for name, frame in (("low_light", low_light), ("backlight", backlight),
                                ("extreme_angle", tilted), ("partial_face", partial))]


def load_appearance_dataset(face):
    """FICTIF : occlusions simples, sans prétendre représenter de vrais visages."""
    glasses, mask, beard = face.copy(), face.copy(), face.copy()
    glasses[28:38, 16:80] = 15
    mask[48:82, 16:80] = (210, 190, 130)
    beard[65:90, 24:72] = 35
    return [Trial("look-" + name, name, "person_a", "person_a", _fixture(frame))
            for name, frame in (("glasses", glasses), ("surgical_mask", mask), ("beard", beard))]


def load_demo_dataset():
    rng = np.random.default_rng(42)
    a, b, stranger = (rng.integers(40, 211, (96, 96, 3), dtype=np.uint8) for _ in range(3))
    trials = [Trial("nominal-a", "nominal", "person_a", "person_a", _fixture(a)),
              Trial("nominal-b", "nominal", "person_b", "person_b", _fixture(b)),
              Trial("impostor-known", "impostor_registered", "person_a", "person_b", _fixture(b)),
              Trial("impostor-new", "impostor_unknown", "person_a", "person_c", _fixture(stranger)),
              Trial("claim-unknown", "unknown_claim", "person_c", "person_a", _fixture(a))]
    trials += load_spoofing_dataset(a) + load_environment_dataset(a) + load_appearance_dataset(a)
    return Dataset({"person_a": _fixture(a), "person_b": _fixture(b)}, trials, simulated=True)
