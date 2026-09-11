"""Prototype local de reconnaissance faciale : capture, entraînement, webcam."""
import argparse
import math
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parent


def sample_count(value):
    number = int(value)
    if not 1 <= number <= 500:
        raise argparse.ArgumentTypeError("Le nombre de captures doit être compris entre 1 et 500.")
    return number


def threshold_value(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Le seuil doit être un nombre positif fini.")
    return number


def person_name(value):
    if not re.fullmatch(r"[\w -]{1,60}", value, re.UNICODE) or not value.strip():
        raise argparse.ArgumentTypeError("Nom : 1 à 60 lettres, chiffres, espaces, - ou _.")
    return value.strip()


def prepare(gray):
    return cv2.equalizeHist(cv2.resize(gray, (200, 200)))


def detector():
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if cascade.empty():
        raise RuntimeError("Le détecteur de visages OpenCV est introuvable.")
    return cascade


def webcam(args, model=None):
    cascade = detector()
    camera = cv2.VideoCapture(args.camera)
    count, last_capture = 0, 0.0
    folder = args.data / "faces" / args.nom if model is None else None
    try:
        if not camera.isOpened():
            raise RuntimeError("Webcam inaccessible. Vérifie les permissions ou essaie --camera 1.")
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
        print("Appuie sur Q ou Échap dans la fenêtre pour arrêter.")
        while True:
            ok, frame = camera.read()
            if not ok:
                raise RuntimeError("Impossible de lire une image de la webcam.")
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80))
            for x, y, w, h in faces:
                face = prepare(gray[y:y+h, x:x+w])
                color = (0, 200, 0)
                if model is None:
                    label = f"Captures : {count}/{args.samples}"
                    if len(faces) == 1 and time.monotonic() - last_capture >= 0.3:
                        path = folder / f"{uuid4().hex}.png"
                        if not cv2.imwrite(str(path), face):
                            raise RuntimeError(f"Impossible d'enregistrer {path}.")
                        count += 1
                        last_capture = time.monotonic()
                else:
                    identity, distance = model.predict(face)
                    name = model.getLabelInfo(identity) if identity >= 0 else ""
                    known = bool(name) and distance <= args.threshold
                    label = f"{name} ({distance:.0f})" if known else "Inconnu"
                    color = (0, 200, 0) if known else (0, 0, 255)
                cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)
                # La police intégrée OpenCV ne gère pas tous les accents.
                label = label.encode("ascii", "replace").decode("ascii")
                cv2.putText(frame, label, (x, max(25, y-10)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            if model is None:
                status = f"{count}/{args.samples} - Un seul visage requis - Q : quitter"
                cv2.putText(frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 255), 2)
            cv2.imshow("Reconnaissance faciale", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                break
            if cv2.getWindowProperty("Reconnaissance faciale", cv2.WND_PROP_VISIBLE) < 1:
                break
            if model is None and count >= args.samples:
                break
    finally:
        camera.release()
        cv2.destroyAllWindows()
    if model is None:
        print(f"{count} nouvelles captures pour {args.nom}. Lance ensuite : python main.py entrainer")


def train(args):
    images, labels, names = [], [], {}
    folders = sorted((args.data / "faces").glob("*"))
    for folder in folders:
        if not folder.is_dir():
            continue
        samples = []
        for path in sorted(folder.glob("*.png")):
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise RuntimeError(f"Image illisible : {path}")
            samples.append(prepare(image))
        if not samples:
            continue
        identity = len(names)
        names[identity] = folder.name
        images.extend(samples)
        labels.extend([identity] * len(samples))
    if not images:
        raise RuntimeError('Aucun visage enregistré. Lance : python main.py enregistrer --nom "Eljah"')
    model = cv2.face.LBPHFaceRecognizer_create()
    model.train(images, np.array(labels, dtype=np.int32))
    for identity, name in names.items():
        model.setLabelInfo(identity, name)
    args.data.mkdir(parents=True, exist_ok=True)
    temporary = args.data / f"model-{uuid4().hex}.yml"
    try:
        model.write(str(temporary))
        temporary.replace(args.data / "model.yml")
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Modèle entraîné : {len(names)} personne(s), {len(images)} images.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("interface", "enregistrer", "entrainer", "reconnaitre"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--data", type=Path, default=ROOT / "data", help="Dossier local des données")
        if command in ("enregistrer", "reconnaitre"):
            sub.add_argument("--camera", type=int, default=0, help="Index de la webcam (défaut : 0)")
        if command == "enregistrer":
            sub.add_argument("--nom", type=person_name, required=True)
            sub.add_argument("--samples", type=sample_count, default=1,
                             help="Nombre de captures, de 1 à 500 (défaut : 1)")
        if command == "reconnaitre":
            sub.add_argument("--threshold", type=threshold_value, default=65, help="Distance maximale acceptée (défaut : 65)")
    args = parser.parse_args()
    if args.command == "interface":
        try:
            from gui import launch
            launch(args.data)
        except ImportError:
            print("Dépendance manquante : installe requirements.txt et le paquet Tkinter de ton Python.", file=sys.stderr)
            return 1
        except (RuntimeError, OSError) as error:
            print(f"Erreur : {error}", file=sys.stderr)
            return 1
        return 0
    global cv2, np
    try:
        import cv2
        import numpy as np
        if not hasattr(cv2, "face"):
            raise RuntimeError("Installe opencv-contrib-python dans un environnement virtuel propre.")
        if args.command == "entrainer":
            train(args)
        elif args.command == "enregistrer":
            webcam(args)
        else:
            path = args.data / "model.yml"
            if not path.is_file():
                raise RuntimeError("Modèle absent. Lance d'abord : python main.py entrainer")
            model = cv2.face.LBPHFaceRecognizer_create()
            model.read(str(path))
            webcam(args, model)
    except ImportError:
        print("Dépendances manquantes : python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    except (RuntimeError, OSError, cv2.error) as error:
        print(f"Erreur : {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nArrêt demandé.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
