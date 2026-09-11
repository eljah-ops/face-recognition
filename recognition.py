"""Qualité de capture et stabilisation des résultats, indépendantes de l'interface."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
from uuid import uuid4

import cv2

DEFAULTS = {"camera": 0, "samples": 1, "threshold": 65.0, "sharpness": 60.0, "confirmations": 5}


def validate_settings(values):
    result = dict(DEFAULTS)
    for key in result:
        value = float(values.get(key, result[key]))
        if not math.isfinite(value):
            raise ValueError(f"Valeur invalide : {key}")
        if key in ("camera", "samples", "confirmations"):
            if not value.is_integer():
                raise ValueError(f"Un entier est requis : {key}")
            value = int(value)
        result[key] = value
    limits = {"camera": (0, 20), "samples": (1, 500), "threshold": (1, 500),
              "sharpness": (0, 2000), "confirmations": (2, 30)}
    for key, (low, high) in limits.items():
        if not low <= result[key] <= high:
            raise ValueError(f"{key} doit être compris entre {low} et {high}.")
    return result


def load_settings(data):
    path = Path(data) / "settings.json"
    if not path.exists():
        return dict(DEFAULTS)
    try:
        return validate_settings(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Réglages illisibles. Les valeurs par défaut ont été chargées.") from error


def save_settings(data, values):
    values = validate_settings(values)
    data = Path(data)
    data.mkdir(parents=True, exist_ok=True)
    temporary = data / f"settings-{uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(values, indent=2), encoding="utf-8")
        temporary.replace(data / "settings.json")
    finally:
        temporary.unlink(missing_ok=True)
    return values


def quality(face, minimum_sharpness=60):
    brightness = float(face.mean())
    sharpness = float(cv2.Laplacian(face, cv2.CV_64F).var())
    if brightness < 45:
        return False, "Ajoute de la lumière", sharpness
    if brightness > 215:
        return False, "Réduis l'éclairage sur le visage", sharpness
    if sharpness < minimum_sharpness:
        return False, "Image floue : stabilise ton visage", sharpness
    return True, "Qualité correcte", sharpness


def overlap(a, b):
    x, y = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[0]+a[2], b[0]+b[2]), min(a[1]+a[3], b[1]+b[3])
    intersection = max(0, right-x) * max(0, bottom-y)
    return intersection / max(1, a[2]*a[3]+b[2]*b[3]-intersection)


@dataclass
class Confirmation:
    required: int = 5
    name: str = ""
    count: int = 0
    box: tuple | None = None

    def reset(self):
        self.name, self.count, self.box = "", 0, None

    def update(self, name, box):
        if not name:
            self.reset()
            return False
        if name != self.name or self.box is None or overlap(self.box, box) < 0.35:
            self.count = 0
        self.name, self.box = name, box
        self.count += 1
        return self.count >= self.required
