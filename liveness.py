"""PAD passif anti-spoof-mn3 via OpenCV DNN, poids officiels vérifiés.

Contrat du modèle : Open Model Zoo, anti-spoof-mn3 (ONNX original).
https://github.com/openvinotoolkit/open_model_zoo/tree/master/models/public/anti-spoof-mn3
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path

import cv2
import numpy as np

MODEL_URL = "https://storage.openvinotoolkit.org/repositories/open_model_zoo/public/2022.1/anti-spoof-mn3/anti-spoof-mn3.onnx"
MODEL_SIZE = 12270179
MODEL_SHA384 = "6de4534964b723397b3e8c995cadcf43bc007cc2f9930b95ae25f76adccece5d1d4d058d0b15117b9e4a9f758424f92a"
DEFAULT_MODEL = Path(__file__).resolve().parent / "models" / "anti-spoof-mn3.onnx"


def verified_weights(path):
    path = Path(path)
    if not path.is_file():
        raise RuntimeError("Modèle PAD absent. Lance : python -m scripts.download_pad")
    if path.stat().st_size != MODEL_SIZE:
        raise RuntimeError("Taille du modèle PAD invalide : téléchargement refusé ou incomplet.")
    payload = path.read_bytes()
    if hashlib.sha384(payload).hexdigest() != MODEL_SHA384:
        raise RuntimeError("Empreinte du modèle PAD invalide. Aucune authentification autorisée.")
    return payload


@dataclass(frozen=True)
class PadDecision:
    is_live: bool
    probability: float


class PassiveLiveness:
    simulated = False

    def __init__(self, model_path=DEFAULT_MODEL, threshold=0.95):
        if not np.isfinite(threshold) or not 0.5 <= threshold <= 1:
            raise ValueError("Seuil PAD requis entre 0.5 et 1.")
        # Charger exactement les octets vérifiés, pas un chemin relu ensuite.
        payload = verified_weights(model_path)
        try:
            self.net = cv2.dnn.readNetFromONNX(np.frombuffer(payload, dtype=np.uint8))
            self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        except cv2.error as error:
            raise RuntimeError("Le modèle PAD ne peut pas être chargé par OpenCV.") from error
        self.threshold = float(threshold)

    def predict(self, face):
        if not isinstance(face, np.ndarray) or face.dtype != np.uint8 or face.ndim != 3 or face.shape[2] != 3 or face.size == 0:
            raise ValueError("Le PAD exige une image BGR uint8 non vide.")
        resized = cv2.resize(face, (128, 128), interpolation=cv2.INTER_CUBIC)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32)
        rgb = (rgb - np.array([151.2405, 119.5950, 107.8395], np.float32)) / np.array([63.0105, 56.4570, 55.0035], np.float32)
        blob = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None])
        try:
            self.net.setInput(blob)
            scores = np.asarray(self.net.forward()).reshape(-1)
        except cv2.error as error:
            raise RuntimeError("Échec de l'inférence PAD : accès refusé.") from error
        # Le modèle officiel renvoie déjà deux probabilités [réel, attaque].
        if (scores.shape != (2,) or not np.isfinite(scores).all()
                or (scores < 0).any() or (scores > 1).any() or not np.isclose(scores.sum(), 1, atol=0.001)):
            raise RuntimeError("Réponse PAD invalide : accès refusé.")
        probability = float(scores[0])
        return PadDecision(probability >= self.threshold, probability)
