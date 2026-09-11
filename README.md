# Face Studio — Premium 3.0

Prototype local avec webcam : enregistrement de visages, entraînement LBPH et affichage du nom des personnes reconnues. Les visages non reconnus sont affichés comme « Inconnu ».

Le code est destiné à un dépôt GitHub privé ; les captures, modèles et exports restent locaux.
Voir le [rapport d'analyse et les améliorations prioritaires](docs/AUDIT.md) et les
[consignes de sécurité](SECURITY.md). Ce prototype ne doit pas servir à autoriser un accès.

## Installation

Dans ce dossier, avec Python 3.11 ou plus récent :

```bash
python3 -m venv /tmp/face-recognition-venv
source /tmp/face-recognition-venv/bin/activate
python -m pip install -r requirements.txt
```

Sur Windows, crée-le avec `python -m venv .venv`, puis active l'environnement avec `.venv\Scripts\activate` dans cmd. Une session graphique et une webcam sont nécessaires à la capture et à la reconnaissance. N'installe pas plusieurs variantes d'OpenCV dans le même environnement : ce projet utilise `opencv-contrib-python`, avec interface graphique.

## Interface Premium

```bash
source /tmp/face-recognition-venv/bin/activate
python main.py interface
```

1. **Aperçu** : vérifie le cadrage et l'éclairage dans la vidéo intégrée.
2. Saisis un nom, puis **Enregistrer** : une seule capture suffit par défaut et la caméra s'arrête automatiquement après son enregistrement. Seules les images avec un seul visage, suffisamment nettes et éclairées sont conservées. La progression s'affiche sous la vidéo.
3. **Entraîner** : construit le modèle avec toutes les personnes actives.
4. **Reconnaître** : confirme une identité après plusieurs prédictions successives cohérentes. Le résultat et la distance sont affichés sous la vidéo.
5. **Arrêter** : libère la caméra ; l'interface reste disponible pendant les opérations.

### Nouveautés 3.0

- Interface bleu nuit et indigo, textes agrandis et contrastés, compteurs lisibles et vidéo qui s’adapte à la fenêtre.
- Onglets **Session**, **Réglages** et **Personnes** ; les formulaires défilent si nécessaire. Le bouton **Enregistrer le visage** se trouve dans Session.
- Panneau **Activité et résultats** repliable ; il s’ouvre automatiquement en cas d’erreur. La touche Échap arrête l’opération en cours.
- État de veille illustré, débit d’images, netteté et distance de reconnaissance.
- Rejet des captures trop sombres, trop claires ou floues, avec indications à l'écran.
- Confirmation temporelle : par défaut, cinq images successives acceptées avec continuité spatiale du visage. Une image inconnue, de mauvaise qualité, un déplacement important ou plusieurs visages réinitialisent la confirmation.
- Réglages mémorisés dans `data/settings.json`, avec validation des valeurs et retour aux valeurs par défaut si le fichier est invalide.
- Résultats de session exportables en CSV. Au plus une entrée par personne toutes les 30 secondes, 1 000 entrées récentes conservées en mémoire. L'historique n'est enregistré sur disque que lors d'un export explicite.
- Traitement vidéo dans un processus séparé, arrêt coopératif puis arrêt forcé si nécessaire, nettoyage de l'aperçu temporaire à la fin de l'opération.

Les réglages s'enregistrent lors du lancement d'une opération. Un seuil de distance plus bas accepte moins de correspondances. La netteté minimale peut être ajustée selon la caméra ; 0 désactive uniquement le filtre de netteté. Le contrôle de luminosité reste actif.

Le nombre de captures se règle de **1 à 500**, avec **1** par défaut. L'entraînement fonctionne dès une image par personne. Tu peux ajouter d'autres captures du même visage pour mieux couvrir les variations d'angle et d'éclairage ; une image seule ne garantit pas la reconnaissance dans toutes les conditions.

Sélectionner une personne remplit le champ du nom pour ajouter des captures. Réentraîne après chaque ajout : le modèle précédent reste utilisable jusqu'au nouvel entraînement. Les captures et modèles V1/V2 sont conservés et compatibles.

**Archiver la sélection** retire la personne après confirmation. Ses captures et l'ancien modèle sont déplacés dans `data/archives/`. La reconnaissance nécessite alors un nouvel entraînement. L'archivage conserve les données ; ce n'est pas une suppression définitive.

Tkinter est nécessaire à l'interface et est disponible dans l'environnement de cette machine. Si ton Python ne l'inclut pas, installe le paquet Tkinter correspondant via ton système. Pour un autre dossier : `python main.py interface --data ./autres-donnees`.

### Limites précises

« Premium » désigne cette édition de l'application, pas une certification de précision. Le moteur reste Haar + LBPH. La confirmation sur plusieurs images réduit les fluctuations ; elle ne constitue ni une preuve d'identité ni une détection du vivant. Une photo peut tromper ce système. La qualité et les seuils doivent être évalués avec de vrais participants enregistrés et inconnus.

L'interface confirme une seule personne à la fois pour éviter d'associer les résultats de plusieurs visages. Les anciennes commandes ci-dessous conservent leur comportement V1/V2, sans les filtres et la confirmation de l'interface Premium. Utilise une seule instance à la fois sur un même dossier de données.

## Utilisation en ligne de commande

```bash
python main.py enregistrer --nom "Eljah"
python main.py enregistrer --nom "Aminata"
python main.py entrainer
python main.py reconnaitre
```

Chaque enregistrement ajoute **1 capture par défaut** puis s'arrête automatiquement. Reste seul devant la caméra avec un bon éclairage. Une seule image par personne permet l'entraînement. Pour plusieurs captures, ajuste `--samples` entre 1 et 500 et change légèrement d'angle et d'expression. Appuie sur **Q** ou **Échap** dans la fenêtre pour quitter ; les captures déjà réalisées sont conservées. Réentraîne après tout ajout ou suppression de personnes.

Options :

```bash
python main.py enregistrer --nom "Eljah" --samples 1 --camera 1
python main.py reconnaitre --threshold 55 --camera 1
python main.py entrainer --data ./autres-donnees
```

Si tu utilises `--data`, donne le même dossier aux trois commandes.

## Fonctionnement et limites

Un détecteur Haar trouve les visages de face. Les images sont converties en niveaux de gris, redimensionnées et leur contraste normalisé. LBPH compare les textures aux exemples enregistrés. Le nombre affiché est une distance : plus elle est faible, plus la correspondance est proche ; ce n'est pas un pourcentage de certitude. [Documentation officielle OpenCV](https://docs.opencv.org/4.13.0/df/d25/classcv_1_1face_1_1LBPHFaceRecognizer.html).

Le seuil de 65 est un point de départ à ajuster avec des personnes enregistrées et non enregistrées. Un seuil plus bas réduit les correspondances acceptées. Ce prototype peut confondre des personnes, dépend de la lumière et de la pose, et ne détecte pas la présentation d'une photo : il ne suffit pas pour sécuriser un accès.

Enregistre uniquement des participants d'accord. Les captures sont conservées dans `data/faces/<nom>/`, le modèle dans `data/model.yml`. Aucun envoi réseau n'est effectué par l'application. Ces données sont exclues de Git, mais ne sont pas chiffrées. Pour retirer une personne, supprime son dossier puis réentraîne ; si tu retires toutes les personnes, supprime aussi le modèle.

## Vérifications sans webcam

```bash
python -m unittest discover -s tests -v
```

Les tests vérifient entraînement et rechargement, archivage, réglages, filtrage de qualité, confirmation temporelle et cycle de vie de la caméra simulée. Ils ne mesurent pas la précision sur de vrais visages.

Les tests de sécurité vérifient aussi le refus de données privées ajoutées de force,
de modèles renommés, de liens symboliques et de secrets encore présents dans l'index Git.

## Banc QA de reconnaissance et anti-spoofing

```bash
python -m qa.validate --demo --output reports/face-qa
```

Le banc couvre enrôlement et matching 1:1, imposteurs, rejeux photo/vidéo,
conditions extrêmes et changements d'apparence. Il produit des rapports **JSON et
JUnit**, le **FAR**, le **FRR** et la **latence moyenne d'inférence**. Les seuils
et effectifs requis sont configurés dans `qa/policy.toml`.

La démonstration utilise des données fictives et retourne volontairement le code
**2** : elle ne valide pas un déploiement. L'application actuelle ne dispose pas
du PAD et du matching 1:1 exigés. L'adaptateur applicatif est à connecter ; ces
capacités manquantes restent bloquantes. Les chargeurs fictifs, le format des vrais
datasets et la procédure d'intégration sont décrits dans [le guide QA](docs/QA.md).

## Préparer un envoi GitHub

Conserve le dépôt en **privé**. Avant chaque commit :

```bash
git add .
python scripts/check_repository.py
git diff --cached --stat
```

Le contrôle porte sur les fichiers préparés dans l'index, pas sur les anciennes
versions de l'historique. Il ne remplace pas la revue du contenu. Les fichiers
sensibles restent exclus même lorsque les données sont placées dans un dossier
personnalisé à l'intérieur du projet. Un dossier extérieur reste préférable.

Pour lancer les audits :

```bash
python -m pip install -r requirements-dev.txt
python -m pip_audit --strict -r requirements.txt -r requirements-dev.txt
python -m bandit -r main.py gui.py recognition.py worker.py ui.py scripts qa -ll
```

L'audit des dépendances consulte les bases de vulnérabilités en ligne ; il n'envoie
pas les captures ni les modèles. Les versions directes sont fixées dans les fichiers
`requirements*.txt`. GitHub Actions est configuré pour exécuter les tests sur Python
3.11 et 3.14 ainsi que les contrôles de sécurité. Dependabot propose les mises à jour.
Les réglages GitHub à activer séparément sont décrits dans [SECURITY.md](SECURITY.md).

## Organisation du code

- `main.py` : commandes historiques, préparation et entraînement LBPH.
- `gui.py` : interface, gestion des processus, archivage et export de session.
- `recognition.py` : qualité, confirmation temporelle et réglages persistants.
- `worker.py` : acquisition vidéo et traitement de la version Premium.
- `ui.py` : thème graphique, typographie adaptée au bureau et aperçu vidéo redimensionnable.
- `tests/` : tests automatisés sans accès à une vraie webcam.
- `scripts/check_repository.py` : contrôle des fichiers préparés pour Git.
- `.github/` : tests, audits et configuration Dependabot.
- `docs/AUDIT.md` : constats, limites et améliorations prioritaires.
- `qa/` : scénarios, chargeurs, contrat d'adaptateur et rapports de validation.
- `docs/QA.md` : exécution du banc et intégration de datasets réels.
