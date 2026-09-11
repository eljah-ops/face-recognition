"""Vérifie les objets de l'index Git, sans afficher le contenu des secrets.

À lancer après git add et avant chaque commit ou premier push.
Ce contrôle ciblé n'est pas un détecteur exhaustif de secrets.
"""
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

MAX_BYTES = 1024 * 1024
PRIVATE_DIRS = {
    "data", "models", "verification", "faces", "archives", "exports", "qa_datasets", "reports", ".ssh", ".aws", ".venv", "venv",
    "__pycache__", ".agents", ".codex", ".idea", ".vscode",
}
PRIVATE_NAMES = (
    ".env", ".env.*", "*.env", "settings.json", "model*.yml", "model*.yaml",
    "credentials*", "secrets.*", "id_rsa*", "id_ed25519*",
)
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(rb'''(?im)["']?(?:api[_-]?key|password|secret|access[_-]?token)["']?\s*[:=]\s*["'][^\s"']{8,}["']'''),
)
MODEL_PATTERN = re.compile(rb"(?mi)^\s*opencv_lbphfaces\s*:")


def path_problem(name):
    path = PurePosixPath(name.lower())
    if any(part in PRIVATE_DIRS for part in path.parts):
        return "dossier privé ou données locales"
    if any(fnmatch(path.name, pattern) for pattern in PRIVATE_NAMES):
        return "configuration privée, modèle ou identifiants"
    # Ce dépôt contient uniquement du code et du texte ; les visuels doivent
    # faire l'objet d'une revue avant d'élargir cette liste.
    if path.name in {".gitignore", ".gitattributes"}:
        return None
    if path.suffix in {".py", ".md", ".txt", ".toml"}:
        return None
    if path.parts[0] == ".github" and path.suffix in {".yml", ".yaml"}:
        return None
    return "type de fichier non autorisé à la publication"


def content_problem(content):
    if len(content) > MAX_BYTES:
        return "fichier supérieur à 1 Mio"
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return "fichier binaire ou texte non UTF-8"
    if b"\0" in content:
        return "contenu binaire"
    if MODEL_PATTERN.search(content):
        return "modèle facial OpenCV"
    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
        return "secret potentiel (valeur masquée)"
    return None


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, timeout=30,
    ).stdout


def check_index(root):
    entries = git(root, "ls-files", "--stage", "-z").split(b"\0")
    issues, count = [], 0
    for entry in filter(None, entries):
        metadata, raw_name = entry.split(b"\t", 1)
        mode, sha, stage = metadata.decode("ascii").split()
        name = raw_name.decode("utf-8", errors="surrogateescape")
        count += 1
        problem = path_problem(name)
        if stage != "0":
            problem = "conflit Git non résolu"
        elif mode not in {"100644", "100755"}:
            problem = "lien symbolique ou sous-module non autorisé"
        if not problem:
            size = int(git(root, "cat-file", "-s", sha))
            problem = "fichier supérieur à 1 Mio" if size > MAX_BYTES else content_problem(
                git(root, "cat-file", "blob", sha)
            )
        if problem:
            issues.append((name, problem))
    if not count:
        issues.append(("index Git", "aucun fichier à vérifier ; lancer git add d'abord"))
    return count, issues


def main():
    try:
        root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
        count, issues = check_index(root)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("Contrôle impossible : vérifier le dépôt Git et son index.", file=sys.stderr)
        return 1
    for name, reason in issues:
        print(f"REFUS {name!r} : {reason}", file=sys.stderr)
    if issues:
        return 1
    print(f"Index vérifié : {count} fichiers, aucune donnée interdite ni secret reconnu.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
