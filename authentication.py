"""Enrôlement avec PAD et authentification LBPH strictement 1:1.

Ce moteur est partagé par le parcours webcam et le banc QA. Les seuils restent
à valider sur des acquisitions réelles avant un usage de contrôle d'accès.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import tempfile
from uuid import uuid4

import cv2
import numpy as np

from liveness import DEFAULT_MODEL, PassiveLiveness
from main import person_name
from recognition import Confirmation, quality, validate_settings


@dataclass(frozen=True)
class AuthenticationDecision:
    accepted: bool
    matched_identity: str | None = None
    liveness_passed: bool | None = None
    reason: str = "Identité non enregistrée pour l'authentification."
    distance: float | None = None
    live_probability: float | None = None


def confined_path(data, *parts):
    """Refuser les liens internes redirigeant modèles ou captures ailleurs."""
    root = Path(data).resolve()
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError("Lien symbolique interdit dans le stockage d'authentification.")
    if not current.resolve().is_relative_to(root):
        raise RuntimeError("Chemin extérieur au dossier de données.")
    return current


def verification_path(data, identity):
    identity = person_name(identity)
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return confined_path(data, "verification", key + ".yml")


@contextmanager
def enrollment_lock(data):
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = confined_path(data, ".authentication.lock")
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as error:
        raise RuntimeError("Une inscription est déjà en cours dans ce dossier.") from error
    try:
        yield
    finally:
        lock.rmdir()


def prepared_face(gray):
    return cv2.equalizeHist(cv2.resize(gray, (200, 200)))


class FaceAuthenticator:
    def __init__(self, data, settings=None, pad_model=DEFAULT_MODEL, pad_threshold=0.95):
        self.data = Path(data).resolve()
        self.settings = validate_settings(settings or {})
        self.cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if self.cascade.empty():
            raise RuntimeError("Détecteur Haar indisponible.")
        self.pad_error = ""
        try:
            self.pad = PassiveLiveness(pad_model, pad_threshold)
        except RuntimeError as error:
            self.pad, self.pad_error = None, str(error)

    @property
    def supports_liveness(self):
        return self.pad is not None

    def require_liveness(self):
        if self.pad is None:
            raise RuntimeError(self.pad_error or "Détection du vivant indisponible : accès refusé.")

    def inspect(self, frame):
        if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3 or frame.size == 0:
            raise ValueError("Image webcam BGR uint8 requise.")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        boxes = self.cascade.detectMultiScale(gray, 1.1, 5, minSize=(100, 100))
        if len(boxes) != 1:
            return None, None, "Un seul visage entier doit être visible.", None
        x, y, width, height = map(int, boxes[0])
        if x <= 0 or y <= 0 or x + width >= frame.shape[1] or y + height >= frame.shape[0]:
            return None, None, "Recadre le visage entier, sans couper ses contours.", None
        crop = gray[y:y + height, x:x + width]
        good, reason, _ = quality(crop, self.settings["sharpness"])
        if not good:
            return None, None, reason, None
        self.require_liveness()
        # Le modèle PAD utilise la couleur et un peu de contexte, pas l'image
        # grise/normalisée du recognizer LBPH. Agrandissement du cadre de 20 %.
        left, top = max(0, x - width // 10), max(0, y - height // 10)
        right, bottom = min(frame.shape[1], x + width + width // 10), min(frame.shape[0], y + height + height // 10)
        pad = self.pad.predict(frame[top:bottom, left:right])
        if not pad.is_live:
            return None, (x, y, width, height), "Présentation suspecte : accès refusé par le PAD.", pad
        return prepared_face(crop), (x, y, width, height), "Vivant détecté ; vérification de l'identité.", pad

    def enroll(self, identity, frames):
        identity = person_name(identity)
        self.require_liveness()
        images = []
        for frame in frames:
            if len(images) >= 500:
                raise ValueError("L'inscription accepte au maximum 500 captures.")
            image, _, reason, _ = self.inspect(frame)
            if image is None:
                raise ValueError(reason)
            images.append(image)
        if not images:
            raise ValueError("L'inscription exige au moins une capture.")
        # Un modèle distinct par identité : aucune recherche dans une galerie.
        model = cv2.face.LBPHFaceRecognizer_create()
        model.train(images, np.zeros(len(images), dtype=np.int32))
        model.setLabelInfo(0, identity)
        with enrollment_lock(self.data):
            target = verification_path(self.data, identity)
            if target.exists():
                raise ValueError("Ce profil est déjà inscrit. Archive-le avant une nouvelle inscription.")
            folder = confined_path(self.data, "faces", identity)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            installed = []
            with tempfile.TemporaryDirectory(prefix=".enrollment-", dir=self.data) as temporary:
                staging = Path(temporary)
                model.write(str(staging / "profile.yml"))
                try:
                    for face in images:
                        name = uuid4().hex + ".png"
                        if not cv2.imwrite(str(staging / name), face):
                            raise RuntimeError("Impossible d'enregistrer une capture d'inscription.")
                        destination = folder / name
                        (staging / name).replace(destination)
                        installed.append(destination)
                    (staging / "profile.yml").replace(target)
                except Exception:
                    for destination in installed:
                        destination.unlink(missing_ok=True)
                    raise

    def session(self, identity):
        return AuthenticationSession(self, person_name(identity))

    def authenticate(self, identity, frames):
        if not isinstance(frames, (tuple, list)) or not frames:
            raise ValueError("Une tentative d'authentification doit contenir des images.")
        session = self.session(identity)
        decision = AuthenticationDecision(False)
        for frame in frames:
            decision = session.step(frame)
            if decision.accepted or decision.liveness_passed is False:
                break  # Même règle que l'arrêt du parcours webcam.
        return decision


class AuthenticationSession:
    def __init__(self, authenticator, identity):
        self.authenticator, self.identity = authenticator, identity
        self.gate = Confirmation(authenticator.settings["confirmations"])
        self.path = verification_path(authenticator.data, identity)
        self.model = None
        if self.path.is_file():
            self.model = cv2.face.LBPHFaceRecognizer_create()
            try:
                self.model.read(str(self.path))
                labels = np.asarray(self.model.getLabels()).reshape(-1)
                if not len(labels) or (labels != 0).any() or self.model.getLabelInfo(0) != identity:
                    raise RuntimeError("Le modèle ne correspond pas à l'identité revendiquée.")
            except cv2.error as error:
                raise RuntimeError("Profil d'authentification illisible.") from error

    def step(self, frame):
        if self.model is None or not self.path.is_file():
            self.gate.reset()
            return AuthenticationDecision(False)
        self.authenticator.require_liveness()
        image, box, reason, pad = self.authenticator.inspect(frame)
        if image is None:
            self.gate.reset()
            return AuthenticationDecision(False, liveness_passed=pad.is_live if pad else None,
                                          reason=reason, live_probability=pad.probability if pad else None)
        label, distance = self.model.predict(image)
        if not np.isfinite(distance) or distance < 0:
            self.gate.reset()
            raise RuntimeError("Distance de reconnaissance invalide : accès refusé.")
        matches = label == 0 and distance <= self.authenticator.settings["threshold"]
        confirmed = self.gate.update(self.identity if matches else "", box)
        return AuthenticationDecision(
            confirmed, self.identity if confirmed else None, True,
            "Identité vérifiée avec PAD." if confirmed else
            f"Confirmation {self.gate.count}/{self.gate.required}" if matches else "Identité différente : accès refusé.",
            float(distance), pad.probability,
        )
