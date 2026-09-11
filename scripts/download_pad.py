"""Installation explicite des poids PAD officiels, hors du dépôt Git."""
import argparse
import hashlib
from pathlib import Path
import tempfile
from urllib.parse import urlparse
from urllib.request import urlopen

from liveness import DEFAULT_MODEL, MODEL_SHA384, MODEL_SIZE, MODEL_URL, verified_weights


def _download_payload():
    parsed_url = urlparse(MODEL_URL)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise RuntimeError("URL du modèle PAD non sécurisée : HTTPS requis.")
    with urlopen(MODEL_URL, timeout=30) as response:  # nosec B310 - HTTPS validé ci-dessus
        return response.read(MODEL_SIZE + 1)


def download(destination):
    destination = Path(destination)
    if destination.exists():
        verified_weights(destination)
        return destination
    payload = _download_payload()
    if len(payload) != MODEL_SIZE or hashlib.sha384(payload).hexdigest() != MODEL_SHA384:
        raise RuntimeError("Le téléchargement ne correspond pas à l'empreinte officielle ; fichier non installé.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_MODEL)
    args = parser.parse_args()
    try:
        path = download(args.output)
    except (OSError, RuntimeError) as error:
        parser.exit(1, f"Installation PAD impossible : {error}\n")
    print(f"Modèle PAD installé et SHA-384 vérifié : {path}")


if __name__ == "__main__":
    main()
