# Banc QA avant déploiement

Le point d'entrée est `python -m qa.validate`. Le banc orchestre les scénarios,
contrôle les décisions d'accès et produit `report.json` et `junit.xml`.
**Il fournit une structure de validation exécutable, pas une implémentation de PAD.**

Le moteur actuel de Face Studio expose une identification **1:N**, sans détection
du vivant ni API d'authentification **1:1**. L'adaptateur applicatif fourni reste
donc à connecter : il déclare ces capacités absentes et lève `NotImplementedError`
pour ses points d'intégration. Une campagne utilisant cet adaptateur est bloquée.

## Exécuter la démonstration

Dans un environnement où `requirements.txt` est installé :

```bash
python -m qa.validate --demo --output reports/face-qa
```

Cette commande doit retourner **2**, car une simulation ne valide jamais un
déploiement. `DemoBackend` compare des pixels aléatoires, sans modèle facial et
sans PAD : les attaques de rejeu sont volontairement acceptées par cette doublure
et rejetées par les assertions de sécurité du banc. Ses FAR, FRR et latences sont
des résultats **de simulation uniquement**.

Les fonctions fictives remplaçables se trouvent dans `qa/datasets.py` :

- `load_spoofing_dataset` : une image rejouée et une séquence de huit images.
- `load_environment_dataset` : assombrissement, arrière-plan saturé, rotation de
  75 degrés et occlusion de la majeure partie de l'image.
- `load_appearance_dataset` : occlusions représentant approximativement lunettes,
  masque chirurgical et barbe.

Ces transformations servent à tester l'orchestration. Une copie d'image ne reproduit
pas une attaque physique avec papier HD ou écran. Pour évaluer le PAD, utiliser des
prises de vue réelles des supports d'attaque, avec consentement et protocole documenté.

## Scénarios et décisions attendues

| Scénario | Cohorte | Assertion |
| --- | --- | --- |
| Avant enrôlement | Précondition | Refus pour une identité absente de la base de test. |
| Enrôlement puis nominal immédiat | Légitime | Accepter une nouvelle capture, avec identité revendiquée correcte et PAD positif. |
| `glasses`, `surgical_mask`, `beard` | Légitime | Même identité et PAD positif ; mesurer les faux rejets. |
| `low_light`, `backlight`, `extreme_angle`, `partial_face` | Robustesse | Refus sans erreur dans ces conditions volontairement inutilisables. |
| `printed_photo_hd`, `smartphone_video` | Attaque PAD | Refus d'accès **et** `liveness_passed=False`. |
| `impostor_registered` | Imposteur | B, déjà enrôlé, ne peut pas revendiquer A. |
| `impostor_unknown` | Imposteur | Un sujet non enrôlé ne peut pas revendiquer A. |
| `unknown_claim` | Imposteur | Un sujet enrôlé ne peut pas revendiquer une identité inexistante. |

La politique choisie pour les quatre cas environnementaux est un refus avec nouvelle
capture attendue : ils sont situés hors des conditions d'usage acceptables. Ils restent
séparés du FRR nominal. Pour mesurer une tolérance à une luminosité ou pose encore
exploitable, constituer des essais légitimes supplémentaires dans les conditions d'usage.
Ne pas réduire les attentes pour faire réussir le moteur après observation des résultats.

Un refus dû à l'absence de visage, à une image illisible ou à une exception ne prouve
pas la détection d'une attaque. `None` signifie « PAD non évalué » et ne satisfait
jamais un scénario anti-spoofing. Toute acceptation sans PAD positif, même avec le
bon nom, échoue. Les assertions restent actives sous `python -O`.

## Brancher le vrai système

Créer une factory Python de confiance qui retourne un objet suivant le contrat
`qa.backends.Backend`. Un exemple à compléter est `FaceStudioAdapter`.

```python
from qa.backends import Decision

class MyTestAdapter:
    simulated = False
    # Mettre ces valeurs à True uniquement après intégration des vraies fonctions.
    supports_liveness = False
    supports_one_to_one = False

    def enroll(self, identity, frames):
        # TODO : vrai flux d'inscription ; base de test initialement vide.
        raise NotImplementedError

    def authenticate(self, claimed_identity, frames):
        # TODO : vrai PAD et matching limité à l'identité revendiquée.
        # Retour attendu : Decision(accepted, matched_identity, liveness_passed).
        raise NotImplementedError

    def close(self):
        # TODO : nettoyer uniquement les ressources de cette session de test.
        pass
```

Les médias arrivent sous forme d'un tuple d'images BGR `uint8`, dans leur ordre
d'acquisition. Le moteur reçoit uniquement les images et l'identité revendiquée :
ni identité réelle du sujet, ni scénario, ni résultat attendu. Il doit retourner sa
**décision d'accès effective**, sans que l'adaptateur corrige a posteriori un nom
renvoyé par une recherche 1:N. Une conversion 1:N en succès 1:1 dans l'adaptateur
masquerait précisément le défaut que l'on cherche à tester.

La factory doit initialiser un espace de test vide et isolé, fixer les seuils du
modèle avant la campagne, appliquer un délai maximal à ses appels réseau éventuels
et attendre la fin d'un calcul GPU avant de retourner. Si le PAD requiert une
interaction active, des horodatages ou un capteur particulier, adapter ce contrat
et le protocole d'acquisition ; les fixtures d'images seules ne peuvent pas le valider.

```bash
python -m qa.validate \
  --dataset /chemin/prive/qa_datasets/manifest.json \
  --adapter mon_integration:MyTestAdapter \
  --policy qa/policy.toml \
  --output reports/face-qa
```

Les attributs de capacité sont déclaratifs : une revue de l'intégration et des
datasets reste indispensable. Aucun programme ne peut garantir qu'un adaptateur
fourni comme code Python ne ment pas sur ses capacités ou ses résultats.

## Manifeste de dataset à compléter

Le chargeur réel `load_manifest` lit un JSON local. Les médias sont chargés à la
demande, avant le chronométrage. Tous les chemins doivent rester dans le dossier
du manifeste, y compris après résolution des liens symboliques. Les pseudonymes
et identifiants d'essais sont alphanumériques, avec `_` et `-` autorisés.

Exemple de **structure**, dont les fichiers restent à fournir ; les faibles
effectifs de cet exemple ne suffisent pas à passer la politique par défaut :

```json
{
  "schema_version": 1,
  "simulated": false,
  "enrollments": {
    "person_a": {"frames": ["enrollment/a.png"]},
    "person_b": {"frames": ["enrollment/b.png"]}
  },
  "trials": [
    {"id": "nominal-a", "scenario": "nominal", "claimed_identity": "person_a", "subject_identity": "person_a", "media": {"frames": ["probes/a-new.png"]}},
    {"id": "nominal-b", "scenario": "nominal", "claimed_identity": "person_b", "subject_identity": "person_b", "media": {"frames": ["probes/b-new.png"]}},
    {"id": "glasses-a", "scenario": "glasses", "claimed_identity": "person_a", "subject_identity": "person_a", "media": {"frames": ["appearance/glasses.png"]}},
    {"id": "mask-a", "scenario": "surgical_mask", "claimed_identity": "person_a", "subject_identity": "person_a", "media": {"frames": ["appearance/mask.png"]}},
    {"id": "beard-a", "scenario": "beard", "claimed_identity": "person_a", "subject_identity": "person_a", "media": {"frames": ["appearance/beard.png"]}},
    {"id": "dark-a", "scenario": "low_light", "claimed_identity": "person_a", "subject_identity": "person_a", "media": {"frames": ["environment/dark.png"]}},
    {"id": "backlight-a", "scenario": "backlight", "claimed_identity": "person_a", "subject_identity": "person_a", "media": {"frames": ["environment/backlight.png"]}},
    {"id": "tilt-a", "scenario": "extreme_angle", "claimed_identity": "person_a", "subject_identity": "person_a", "media": {"frames": ["environment/tilt.png"]}},
    {"id": "partial-a", "scenario": "partial_face", "claimed_identity": "person_a", "subject_identity": "person_a", "media": {"frames": ["environment/partial.png"]}},
    {"id": "photo-a", "scenario": "printed_photo_hd", "claimed_identity": "person_a", "subject_identity": "person_a", "media": {"frames": ["attacks/printed-photo-capture.png"]}},
    {"id": "video-a", "scenario": "smartphone_video", "claimed_identity": "person_a", "subject_identity": "person_a", "media": {"video": "attacks/smartphone-capture.mp4"}},
    {"id": "known-impostor", "scenario": "impostor_registered", "claimed_identity": "person_a", "subject_identity": "person_b", "media": {"frames": ["probes/b-other.png"]}},
    {"id": "new-impostor", "scenario": "impostor_unknown", "claimed_identity": "person_a", "subject_identity": "person_c", "media": {"frames": ["probes/c.png"]}},
    {"id": "unknown-id", "scenario": "unknown_claim", "claimed_identity": "person_c", "subject_identity": "person_a", "media": {"frames": ["probes/a-other.png"]}}
  ]
}
```

Chaque essai peut fournir `frames` ou `video`, jamais les deux. Limites : 120 images
et 256 Mio décodés par tentative ; une vidéo trop longue est refusée sans troncature.
Une vidéo de rejeu doit contenir au moins deux images. Découper les enregistrements
en tentatives distinctes selon le protocole, sans compter chaque image comme un essai.

Pour les données réelles, les captures d'enrôlement et d'évaluation doivent être
distinctes. Le banc refuse leurs pixels identiques, même sous un nom de fichier
différent, et les tentatives dupliquées pour une même identité revendiquée/cohorte.
Cette protection ne détecte pas toutes les quasi-copies ni les biais de sélection.
Utiliser plusieurs personnes, sessions, supports et caméras, avec des inconnus
distincts des participants enrôlés. Garder un jeu de calibration séparé du jeu final.

## Métriques, assertions et rapports

- **FAR** = fausses acceptations / essais d'imposteurs ordinaires évalués.
- **FRR** = rejets ou mauvaises identités / essais légitimes nominaux et variations
  d'apparence évalués. Les cas extrêmes exigeant un refus sont rapportés séparément.
- **Taux d'acceptation des attaques** = attaques acceptées / attaques évaluées,
  avec détails par scénario. Une attaque acceptée bloque toujours la validation.
- **Latence moyenne d'inférence**, en ms = moyenne des appels `authenticate`
  ayant retourné une décision exploitable, acceptée ou rejetée. Elle inclut le
  prétraitement, le PAD, le matching et, pour une API distante, l'aller-retour réseau.
  Chargement des médias, rapports, préconditions et enrôlement sont exclus. Les
  premiers appels après enrôlement sont inclus : aucun échauffement caché.

Les taux sont compris entre 0 et 1 ; multiplier par 100 pour un pourcentage. Un
dénominateur nul produit `null`, jamais un taux flatteur de zéro. Les erreurs et
effectifs incomplets sont visibles et bloquent la validation, même si les taux
calculables sur les décisions restantes paraissent bons.

`qa/policy.toml` fixe les seuils FAR/FRR/latence et les effectifs minimaux. Les
valeurs par défaut sont des exigences d'ingénierie proposées, à fixer avant mesure,
pas une norme de certification. Chaque scénario a aussi son assertion individuelle :
un taux global acceptable ne neutralise pas une régression sur un cas requis.
Un nombre minimal d'essais ne garantit pas à lui seul une significativité statistique
ni un faible taux d'erreur en population ; prévoir une analyse d'incertitude indépendante.

Les rapports contiennent les résultats par essai, les effectifs, les erreurs,
les métriques et `release_gate_passed`. Ils n'incluent ni images ni templates ;
les exceptions externes sont résumées par leur type. Utiliser des pseudonymes pour
les IDs, conserver ces rapports localement et contrôler toute diffusion. `reports/`
et `qa_datasets/` sont exclus de Git et refusés par le contrôle de publication.

Codes de sortie : **0** = toutes les exigences configurées passent sur une campagne
non simulée ; **1** = assertions échouées ; **2** = simulation, configuration invalide
ou erreur d'exécution. Un code 0 concerne le périmètre de cette campagne uniquement.

Les tests du banc font partie de `python -m unittest discover -s tests -v`. La CI
existante les exécute ; elle ne lance pas de campagne sur des visages privés et ne
publie pas automatiquement les rapports biométriques.

Références méthodologiques : [NIST FATE, évaluation du PAD](https://pages.nist.gov/frvt/html/frvt_pad.html)
et [NIST FRTE, vérification 1:1](https://pages.nist.gov/frvt/html/frvt11.html).
Elles motivent la séparation entre vérification d'identité et résistance aux
attaques ; ce banc ne revendique aucune conformité à ces évaluations.
