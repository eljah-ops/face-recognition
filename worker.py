"""Processus vidéo isolé : aucune opération Tkinter ici."""
import argparse
import json
from pathlib import Path
import time
import tempfile
from uuid import uuid4

import cv2
import numpy as np
import main as engine
from recognition import Confirmation, quality, validate_settings

engine.cv2, engine.np = cv2, np


def emit(**values):
    print(json.dumps(values, ensure_ascii=False), flush=True)


def run(args):
    if args.mode in ("inscrire", "authentifier"):
        return run_authentication(args)
    settings = validate_settings(json.loads(args.settings))
    cascade = engine.detector()
    model = None
    if args.mode == "reconnaitre":
        path = args.data / "model.yml"
        if not path.is_file():
            raise RuntimeError("Entraîne d'abord le modèle.")
        model = cv2.face.LBPHFaceRecognizer_create()
        model.read(str(path))
    folder = args.data / "faces" / engine.person_name(args.name) if args.mode == "enregistrer" else None
    if folder:
        folder.mkdir(parents=True, exist_ok=True)
    camera = cv2.VideoCapture(settings["camera"])
    gate = Confirmation(settings["confirmations"])
    count, last_capture, last_frame = 0, 0.0, 0.0
    started = time.monotonic()
    frames = 0
    try:
        if not camera.isOpened():
            raise RuntimeError("Caméra inaccessible. Vérifie son index et ses permissions.")
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)
        while not (args.preview / "stop").exists():
            ok, frame = camera.read()
            if not ok:
                raise RuntimeError("La caméra ne fournit plus d'image.")
            frames += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, 1.1, 5, minSize=(100, 100))
            status, name, distance, confirmed = "Place un seul visage face à la caméra", "", None, False
            sharpness = 0.0
            if len(faces) == 1:
                x, y, w, h = (int(v) for v in faces[0])
                crop = gray[y:y+h, x:x+w]
                good, status, sharpness = quality(crop, settings["sharpness"])
                if good:
                    if folder is not None:
                        if time.monotonic() - last_capture >= 0.35:
                            path = folder / f"{uuid4().hex}.png"
                            if not cv2.imwrite(str(path), engine.prepare(crop)):
                                raise RuntimeError("Impossible d'enregistrer la capture.")
                            count += 1
                            last_capture = time.monotonic()
                        guidance = "Capture terminée" if count >= settings["samples"] else "Change légèrement d'angle"
                        status = f"Capture {count}/{settings['samples']} · {guidance}"
                    elif model is not None:
                        identity, distance = model.predict(engine.prepare(crop))
                        candidate = model.getLabelInfo(identity) if identity >= 0 and distance <= settings["threshold"] else ""
                        confirmed = gate.update(candidate, (x, y, w, h))
                        name = candidate if confirmed else ""
                        status = f"Reconnu : {name}" if confirmed else f"Vérification {gate.count}/{gate.required}" if candidate else "Visage inconnu"
                else:
                    gate.reset()
                color = (90, 210, 70) if confirmed or (good and folder is not None) else (30, 180, 245)
                cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)
            else:
                gate.reset()
                if len(faces) > 1:
                    status = "Plusieurs visages : garde une seule personne dans le cadre"
                for x, y, w, h in faces:
                    cv2.rectangle(frame, (x, y), (x+w, y+h), (30, 180, 245), 2)
            now = time.monotonic()
            if now - last_frame >= 0.1:
                ratio = min(760/frame.shape[1], 440/frame.shape[0])
                preview = cv2.resize(frame, (int(frame.shape[1]*ratio), int(frame.shape[0]*ratio)))
                temporary = args.preview / "next.png"
                if not cv2.imwrite(str(temporary), preview):
                    raise RuntimeError("Impossible de préparer l'aperçu vidéo.")
                temporary.replace(args.preview / "frame.png")
                emit(status=status, count=count, name=name, distance=distance if distance is not None and np.isfinite(distance) else None,
                     sharpness=round(sharpness), fps=round(frames/max(0.01, now-started), 1), confirmed=confirmed)
                last_frame = now
            if folder is not None and count >= settings["samples"]:
                break
        saved = "capture enregistrée" if count == 1 else "captures enregistrées"
        emit(status=f"Terminé · {count} {saved}" if folder is not None else "Caméra arrêtée", count=count)
    finally:
        camera.release()


def run_authentication(args):
    """Acquisition seulement ; décisions partagées avec l'adaptateur QA."""
    from authentication import FaceAuthenticator, verification_path
    from liveness import DEFAULT_MODEL

    settings = validate_settings(json.loads(args.settings))
    name = engine.person_name(args.name)
    authenticator = FaceAuthenticator(args.data, settings, getattr(args, "pad_model", None) or DEFAULT_MODEL)
    authenticator.require_liveness()  # Ne jamais ouvrir la caméra sans le PAD.
    path = verification_path(args.data, name)
    if args.mode == "inscrire" and path.exists():
        raise ValueError("Profil déjà inscrit : archive-le avant de recommencer.")
    if args.mode == "authentifier" and not path.is_file():
        raise ValueError("Profil absent : utilise d'abord l'inscription avec contrôle du vivant.")
    session = authenticator.session(name) if args.mode == "authentifier" else None
    preview = args.preview
    camera = cv2.VideoCapture(settings["camera"])
    count, last_capture, last_preview = 0, 0.0, 0.0
    started = time.monotonic()
    try:
        if not camera.isOpened():
            raise RuntimeError("Caméra inaccessible.")
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)
        with tempfile.TemporaryDirectory(prefix="face-studio-enrollment-") as temporary:
            captures = []
            while preview is None or not (preview / "stop").exists():
                ok, frame = camera.read()
                if not ok:
                    raise RuntimeError("La caméra ne fournit plus d'image.")
                now = time.monotonic()
                decision = None
                if session is not None:
                    decision = session.step(frame)
                    status = decision.reason
                    if now - started >= 20 and not decision.accepted:
                        emit(status="Délai dépassé : authentification refusée.", confirmed=False)
                        return False
                else:
                    face, _, status, _ = authenticator.inspect(frame)
                    if face is not None and now - last_capture >= 0.35:
                        capture = Path(temporary) / f"{count}.png"
                        if not cv2.imwrite(str(capture), frame):
                            raise RuntimeError("Impossible de préparer la capture d'inscription.")
                        captures.append(capture)
                        count += 1
                        last_capture = now
                        status = f"Inscription avec PAD : {count}/{settings['samples']}"
                terminal = decision is not None and (decision.accepted or decision.liveness_passed is False)
                if now - last_preview >= 0.1 or terminal:
                    emit(status=status, count=count, name=decision.matched_identity if decision else "",
                         distance=decision.distance if decision else None,
                         confirmed=decision.accepted if decision else False,
                         liveness_passed=decision.liveness_passed if decision else None)
                    if preview is not None:
                        ratio = min(760 / frame.shape[1], 440 / frame.shape[0])
                        small = cv2.resize(frame, (int(frame.shape[1] * ratio), int(frame.shape[0] * ratio)))
                        next_frame = preview / "next.png"
                        if not cv2.imwrite(str(next_frame), small):
                            raise RuntimeError("Impossible de préparer l'aperçu.")
                        next_frame.replace(preview / "frame.png")
                    last_preview = now
                if preview is None:
                    display = frame.copy()
                    cv2.putText(display, status.encode("ascii", "replace").decode(), (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 255), 2)
                    cv2.imshow("Authentification 1:1 avec PAD", display)
                    if (cv2.waitKey(1) & 0xFF) in (ord("q"), ord("Q"), 27):
                        break
                    if cv2.getWindowProperty("Authentification 1:1 avec PAD", cv2.WND_PROP_VISIBLE) < 1:
                        break
                if terminal:
                    return decision.accepted
                if session is None and count >= settings["samples"]:
                    authenticator.enroll(name, (cv2.imread(str(p), cv2.IMREAD_COLOR) for p in captures))
                    emit(status="Inscription terminée. Le profil est prêt pour l'authentification 1:1.", count=count)
                    return True
        emit(status="Opération interrompue ; aucun accès accordé.", confirmed=False)
        return False
    finally:
        camera.release()
        if preview is None:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("apercu", "enregistrer", "reconnaitre", "inscrire", "authentifier"))
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--pad-model", type=Path)
    options = parser.parse_args()
    try:
        result = run(options)
        if options.mode in ("inscrire", "authentifier") and result is False:
            raise SystemExit(2)
    except (OSError, RuntimeError, ValueError, cv2.error) as error:
        emit(error=str(error))
        raise SystemExit(1)
