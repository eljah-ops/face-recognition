# Sécurité et confidentialité

Face Studio est un prototype local de reconnaissance faciale. Il ne constitue pas
un système d'authentification : une photo peut tromper le moteur Haar + LBPH.

## Données à conserver hors de GitHub

Les captures, noms associés au modèle, archives, réglages et exports de session
doivent rester locaux, même si le dépôt GitHub est privé. Le modèle entraîné
contient des informations biométriques : il n'est pas un simple fichier de code.

- Enregistrer uniquement des participants informés et d'accord.
- Conserver les données sur un volume chiffré avec un accès limité au compte utilisateur.
- Privilégier un dossier de données extérieur au dépôt avec `--data`.
- Définir une durée de conservation et supprimer aussi les archives et les sauvegardes
  lorsqu'une suppression définitive est nécessaire ; réentraîner ou retirer le modèle.
- Charger uniquement des modèles et images de confiance. Les données locales ne sont
  pas chiffrées par l'application ; `.gitignore` ne protège pas leur accès sur le disque.

## Avant chaque publication

```bash
git add .
python scripts/check_repository.py
git diff --cached --stat
# Après vérification du contenu :
git commit -m "Décrire la modification"
git push
```

Le script vérifie les **objets de l'index Git**, donc les fichiers effectivement
préparés pour le commit. Il refuse les dossiers privés connus, les modèles LBPH
reconnaissables, les fichiers binaires, les liens symboliques, les fichiers trop
volumineux et plusieurs formats de secrets. Il vérifie aussi un fichier ajouté
avec `git add -f`. La liste des types publiables est volontairement limitée au code,
à la documentation texte et aux configurations prévues.

Cette vérification est manuelle avant commit et automatique dans GitHub Actions
après envoi. Elle ne constitue pas un blocage serveur avant réception et n'analyse
pas l'historique des anciens commits. Une revue humaine reste nécessaire : aucun
scanner ne détecte tous les secrets ou toutes les données personnelles dans du texte.
Un fichier déjà suivi par Git n'est pas protégé simplement en ajoutant une règle
dans `.gitignore`.

## Configuration du dépôt privé

La configuration versionnée prévoit des tests, Bandit, un audit des dépendances
et des propositions hebdomadaires de mises à jour Dependabot. Les actions sont
fixées à un commit et le jeton CI dispose uniquement de `contents: read` ; les
identifiants du checkout ne sont pas conservés.

Réglages à vérifier dans GitHub après création (leur disponibilité dépend de l'offre) :

1. Visibilité **Private**, collaborateurs limités aux personnes nécessaires.
2. Authentification à deux facteurs sur le compte et moyen de récupération conservé.
3. Protection de `main` : pas de force push ni suppression ; contrôles CI obligatoires
   avant fusion si les règles sont disponibles pour ce dépôt privé.
4. Alertes Dependabot et mises à jour de sécurité activées.
5. Détection des secrets et protection des push activées si disponibles.

Le fichier de workflow et `dependabot.yml` ne suffisent pas à activer tous ces
réglages serveur. Ne jamais transmettre un jeton GitHub dans une conversation,
un fichier du dépôt ou l'URL du remote. Utiliser une connexion GitHub CLI ou SSH
avec une portée limitée.

## Signaler un problème

Utiliser le signalement privé de vulnérabilité du dépôt s'il est proposé. Sinon,
contacter le propriétaire par un canal privé déjà connu. Ne pas joindre de
visages, de modèles, de données de session ou de secrets à une issue publique.

Si un secret a été publié, le révoquer immédiatement puis traiter l'historique
et les copies existantes. Supprimer le fichier du dernier commit ne suffit pas.

Références : [sécuriser GitHub Actions](https://docs.github.com/en/actions/reference/security/secure-use),
[configurer Dependabot](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference).
