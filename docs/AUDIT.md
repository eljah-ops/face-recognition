# Analyse du projet Face Studio

Analyse du 11 septembre 2026, fondée sur le code local. La précision de reconnaissance
et le comportement réel avec une webcam n'ont pas été mesurés pendant cet audit.

## Avis général

La structure convient à une démonstration locale : séparation entre l'interface
et le traitement vidéo, libération de la caméra, sauvegarde atomique du modèle,
validation des noms et paramètres, confirmation temporelle et tests sans webcam.
L'export CSV protège déjà les noms commençant par certains caractères de formule.

La prochaine priorité est la confidentialité des données et la mesure de la fiabilité.
L'appellation « Premium » ne change pas les limites du moteur Haar + LBPH. Ce projet
peut être présenté comme prototype, mais son résultat ne prouve pas une identité.

## Améliorations prioritaires

| Priorité | Constat vérifiable | Amélioration et critère de réussite |
| --- | --- | --- |
| Haute | `.gitignore` excluait essentiellement `data/` ; un export CSV ailleurs ou des données sous `--data` pouvaient être ajoutés. Aucun contrôle CI n'était présent. | **Traité localement** : exclusions étendues, contrôle de l'index et workflows de sécurité. La publication et les réglages serveur GitHub restent des étapes distinctes. |
| Haute | Les captures, noms du modèle et archives restent en clair ; `archive_person` conserve les anciennes données. | Définir consentement et durée de conservation, ajouter une suppression définitive explicite incluant archives et anciens modèles, et utiliser un stockage chiffré avec accès limité. Vérifier qu'une personne supprimée n'est plus reconnue après réentraînement. |
| Haute | Haar + LBPH n'a pas de détection du vivant ; la confirmation temporelle ne distingue pas une personne d'une photographie. | Garder un usage de démonstration. Pour un futur contrôle d'accès, concevoir une authentification indépendante et évaluer une détection de présentation frauduleuse avant tout usage réel. |
| Haute | Les tests utilisent des images synthétiques. Le seuil 65 et une capture par défaut ne sont pas calibrés sur des participants réels. | Constituer un jeu d'évaluation consenti, séparer inscription et essais, mesurer faux positifs/faux négatifs selon éclairage, pose et caméra ; choisir ensuite seuil et nombre de captures. |
| Moyenne | Les commandes de `main.webcam` n'appliquent pas les filtres de qualité et la confirmation de `worker.run`. | Partager une même chaîne de reconnaissance entre CLI et interface ; vérifier les mêmes décisions sur une séquence simulée. |
| Moyenne | `train` suit les dossiers/images locaux sans contrôle général des liens symboliques ; plusieurs instances peuvent modifier les mêmes données. | Refuser les chemins internes redirigés et ajouter un verrou par dossier de données. Tester les liens et deux opérations concurrentes sans toucher aux données réelles. |
| Moyenne | `worker` et les tests affectent `main.cv2` et `main.np` ; capture, entraînement et parsing CLI sont réunis dans `main.py`. | Extraire un module de moteur avec dépendances explicites et conserver la CLI comme point d'entrée. Faciliter ensuite les tests et la distribution. |
| Moyenne | Les plages de versions permettaient des installations différentes ; les environnements graphiques varient selon les machines. | **Versions directes fixées** et matrice CI prévue. Compléter ensuite par un verrouillage des dépendances transitives avec empreintes et une validation Windows/Linux avant distribution. |

## Protections ajoutées

- Exclusion des données biométriques, exports, sauvegardes, clés et fichiers `.env`,
  y compris les dossiers `faces/` et `archives/` sous un chemin de données personnalisé.
- Vérification des objets de l'index Git : fichiers privés ajoutés de force, modèle
  LBPH renommé en texte, binaire, symlink, taille excessive et formats courants de secrets.
- Tests de ce contrôle, dont le cas où un secret est retiré du fichier local mais
  demeure dans la version préparée pour le commit.
- Configuration GitHub Actions : tests sans caméra, audit des dépendances, analyse
  statique, autorisations de lecture et actions fixées à un identifiant de commit.
- Configuration Dependabot et documentation de sécurité.

La CI s'exécute après réception du code par GitHub. Il faut donc lancer le contrôle
local avant le premier envoi et relire les fichiers préparés. Le contrôle est ciblé :
il ne détecte pas toutes les données personnelles ni tous les formats de secrets.
Les tests du moteur n'établissent pas une précision biométrique réelle.

## Vérifications locales

- **19 tests réussis** sous Python 3.14.4, avec OpenCV contrib 4.14.0.94 et NumPy 2.4.6.
- `pip check` : aucune incompatibilité de dépendances installées.
- `pip-audit --strict -r requirements.txt -r requirements-dev.txt` : aucune
  vulnérabilité connue détectée au moment de l'audit, dépendances résolues incluses.
- Bandit : **0 alerte moyenne ou haute**, 5 alertes faibles sur les sous-processus.
  Revue effectuée : commandes transmises comme listes d'arguments sans shell ;
  l'exécutable `git` reste résolu depuis le `PATH` de confiance de la machine.
- Index initial : **19 fichiers vérifiés**, aucun fichier privé interdit ni secret
  reconnu par le contrôle ciblé ; le modèle et les réglages locaux sont exclus.
- Configurations YAML lisibles, références des actions vérifiées auprès de leurs
  dépôts officiels. L'exécution réelle sur les runners GitHub reste à confirmer.

Les essais Python 3.11, Windows et webcam ne sont pas validés localement.

## Publication visée

Destination demandée : `eljah-ops/face-recognition`, visibilité **privée**.
Le modèle et les réglages existants ne doivent pas figurer dans le premier commit.
L'état de création, d'envoi et d'exécution CI doit être vérifié séparément ; les fichiers
de configuration présents dans ce projet ne prouvent pas leur activation sur GitHub.

Les réglages complémentaires à vérifier après création sont détaillés dans
[SECURITY.md](../SECURITY.md). Les permissions minimales et le verrouillage des actions
suivent les [recommandations officielles GitHub](https://docs.github.com/en/actions/reference/security/secure-use).
Le calendrier de maintenance suit la
[configuration officielle Dependabot](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference).
