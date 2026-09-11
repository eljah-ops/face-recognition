"""Contrat de l'adaptateur applicatif et doublure explicitement fictive."""
from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Protocol

import numpy as np

from authentication import FaceAuthenticator
from liveness import DEFAULT_MODEL, MODEL_SHA384
from qa.datasets import Frames


@dataclass(frozen=True)
class Decision:
    accepted: bool
    matched_identity: str | None
    liveness_passed: bool | None


class Backend(Protocol):
    simulated: bool
    supports_liveness: bool
    supports_one_to_one: bool

    def enroll(self, identity: str, frames: Frames) -> None: ...
    def authenticate(self, claimed_identity: str, frames: Frames) -> Decision: ...
    def close(self) -> None: ...


class FaceStudioAdapter:
    """Même moteur que la webcam, dans une base temporaire initialement vide."""
    simulated = False
    supports_one_to_one = True

    def __init__(self, pad_model=DEFAULT_MODEL, settings=None, pad_threshold=0.95):
        self.storage = tempfile.TemporaryDirectory(prefix="face-studio-qa-")
        try:
            self.engine = FaceAuthenticator(Path(self.storage.name), settings, pad_model, pad_threshold)
        except Exception:
            self.storage.cleanup()
            raise
        self.audit_metadata = {
            "pad_model": "anti-spoof-mn3", "pad_sha384": MODEL_SHA384,
            "pad_threshold": pad_threshold, "settings": self.engine.settings,
        }

    @property
    def supports_liveness(self):
        return self.engine.supports_liveness

    def enroll(self, identity, frames):
        self.engine.enroll(identity, frames)

    def authenticate(self, claimed_identity, frames):
        result = self.engine.authenticate(claimed_identity, frames)
        return Decision(result.accepted, result.matched_identity, result.liveness_passed)

    def close(self):
        self.storage.cleanup()


class DemoBackend:
    """Doublure sur pixels aléatoires, volontairement dépourvue de PAD.

    Aucun modèle facial n'est évalué ici. Les rejeux seront acceptés et les
    assertions anti-spoofing échoueront, même si le matching fictif réussit.
    """
    simulated = True
    supports_liveness = False
    supports_one_to_one = True

    def __init__(self):
        self.templates = {}

    def enroll(self, identity, frames):
        self.templates[identity] = frames[0].astype(np.float32).copy()

    def authenticate(self, claimed_identity, frames):
        template = self.templates.get(claimed_identity)
        if template is None or template.shape != frames[0].shape:
            return Decision(False, None, None)
        distance = float(np.sqrt(np.mean((template - frames[0].astype(np.float32)) ** 2)))
        accepted = distance <= 45
        return Decision(accepted, claimed_identity if accepted else None, None)

    def close(self):
        self.templates.clear()
