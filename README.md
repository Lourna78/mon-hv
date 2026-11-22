# Mon HV — WoW Forever (EU)

Outil perso de suivi de l'hôtel des ventes de World of Warcraft: Forever.
Toutes les heures, GitHub télécharge les annonces de l'HV via l'API officielle de Blizzard et met à jour un tableau de bord privé en français :

- **Ma liste** : vos objets suivis, avec un prix d'achat cible (la ligne passe en vert quand le prix tombe en dessous)
- **Bonnes affaires** : annonces au moins 25 % sous la médiane des 7 derniers jours
- **À vendre aujourd'hui** : ce qui part vite quand le prix est haut
- **Prix du jour** : recherche, catégories, tri par variation
- **Mouvements de la semaine** : plus fortes hausses et baisses
- **Métiers** : rentabilité de vos recettes (fichier `recipes.json`)
- **Fiche objet** : graphique 30 ou 90 jours, meilleurs jours pour acheter et pour vendre

Coût : 0 €. Tout tourne sur un compte GitHub gratuit.

---

## Installation (environ 15 minutes, une seule fois)

### 1. Créer votre accès à l'API Blizzard
1. Allez sur **https://develop.battle.net** et connectez-vous avec votre compte Battle.net.
2. Menu **API Access** → **Create Client**.
3. Nom : `mon-hv` (au choix). Redirect URL : `http://localhost`. Service URL : laissez vide ou mettez `http://localhost`. Intended use : « Personal auction house price tracker ».
4. Validez. Notez le **Client ID** et le **Client Secret**. Ne les partagez avec personne.

### 2. Créer le dépôt GitHub
1. Créez un compte gratuit sur **https://github.com** si vous n'en avez pas.
2. Bouton **+** en haut à droite → **New repository**. Nom : `mon-hv`. Cochez **Public** (obligatoire pour la page gratuite ; seuls les prix du jeu sont visibles, jamais vos clés).
3. Sur la page du dépôt vide : **uploading an existing file** → glissez **tout le contenu** du dossier (y compris le dossier `.github`) → **Commit changes**.
   - Astuce : si le dossier `.github` ne se glisse pas (il est caché), créez le fichier à la main : **Add file → Create new file**, nom `.github/workflows/collect.yml`, et collez-y le contenu du fichier.

### 3. Ajouter vos clés Blizzard (en secret)
Dans le dépôt : **Settings → Secrets and variables → Actions → New repository secret**, deux fois :
- `BNET_CLIENT_ID` = votre Client ID
- `BNET_CLIENT_SECRET` = votre Client Secret

### 4. Activer la page web
**Settings → Pages** → Source : **GitHub Actions**.

### 5. Trouver le bon numéro d'HV (important)
Blizzard ne documente pas les numéros d'HV de Forever. Le fichier `config.json` part sur **113** (Horde d'après la communauté), à vérifier :
1. Onglet **Actions** → **Collecte HV** → **Run workflow** → cochez **Mode découverte** → **Run workflow**.
2. Ouvrez l'exécution terminée → étape **Mode découverte** : la liste des HV qui répondent s'affiche avec leur nombre d'annonces.
3. Si le numéro de l'HV Horde n'est pas 113, modifiez `config.json` (crayon ✏️ sur GitHub) → `"horde": LE_BON_NUMÉRO`.
   Vous pouvez suivre plusieurs HV : `"horde": 113, "alliance": 110`.

### 6. Lancer la première collecte
**Actions → Collecte HV → Run workflow** (sans cocher la découverte).
Après 2 à 5 minutes, votre tableau de bord est en ligne à l'adresse :
`https://VOTRE-PSEUDO-GITHUB.github.io/mon-hv/`
Ajoutez-la à vos favoris. Ensuite, tout se met à jour seul chaque heure.

---

## Plan du jour

Le premier onglet donne des consignes concrètes, recalculées à chaque collecte :
- **Que vendre aujourd'hui** : les matériaux qui s'écoulent le plus vite, le meilleur créneau horaire pour poster (calculé sur l'historique heure par heure, heure de Paris) et un conseil : vendre maintenant, poster vers telle heure, ou attendre.
- **À acheter pour revendre** : objets qui se vendent bien, affichés au moins 15 % sous leur valeur normale, avec le prix maximum d'achat, le prix de revente visé et le meilleur jour pour revendre.
- **Farmer en mage** : or par heure des routes de `farms.json` aux prix du moment. Remplacez les quantités par heure par vos vrais résultats directement dans le tableau.
- **Crafter avec vos métiers** : les recettes les plus rentables de vos métiers.

Les créneaux horaires deviennent fiables après environ 2 jours de collecte, les jours de la semaine après 14 jours.

## Si la collecte ne se lance pas toute seule (cron-job.org)

GitHub lance normalement la collecte toutes les heures, mais ses tâches planifiées peuvent tarder à démarrer ou sauter des heures. Pour un déclenchement fiable :
1. **Jeton GitHub** : photo de profil → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**. Nom : `mon-hv cron`. Expiration : la plus longue proposée. **Repository access** : *Only select repositories* → `mon-hv`. **Permissions** → **Actions** : *Read and write*. Générez et copiez le jeton (il ne s'affiche qu'une fois).
2. **cron-job.org** : créez un compte gratuit → **Create cronjob**.
   - URL : `https://api.github.com/repos/Lourna78/mon-hv/actions/workflows/collect.yml/dispatches`
   - Planification : toutes les heures, minute 7.
   - Onglet **Advanced** : méthode **POST** ; en-têtes `Accept: application/vnd.github+json`, `Authorization: Bearer VOTRE_JETON`, `X-GitHub-Api-Version: 2022-11-28` ; corps de requête : `{"ref":"main"}`.
3. **Test run** : la réponse doit être `204`, et un lancement apparaît dans l'onglet Actions.

Le jeton ne donne accès qu'aux lancements de ce dépôt. Si GitHub et cron-job.org lancent tous les deux la collecte, ce n'est pas grave : une photo déjà traitée est ignorée.

## Alertes Discord et e-mail

L'outil vous prévient tout seul, à chaque collecte, sur l'HV choisi dans `config.json` (`alerts.house`) :
- **Bonne affaire** : une annonce au moins 40 % sous la valeur de marché des 7 derniers jours, sur un objet qui se vend (au moins 1 par jour), avec au moins 5 po de gain potentiel. Réglages dans `config.json` → `alerts` (`deal_pct`, `min_value`, `min_profit`).
- **Prix cibles** : dans `alerts.json`, une ligne par objet. `"below"` = prévenir quand le prix le plus bas passe sous ce montant (achat), `"above"` = quand il dépasse ce montant (vente). Le numéro d'objet et une ligne prête à copier s'affichent dans la fiche de l'objet du tableau de bord.
- Une même alerte n'est pas renvoyée avant 12 h, sauf si le prix baisse encore de 10 %.

### Discord (recommandé : notification sur le téléphone)
1. Dans Discord, créez un serveur à vous (le « + » à gauche → Créer le mien → Pour moi et mes amis), nommé par exemple « Mon HV ».
2. Sur le salon `#général` : roue dentée **Modifier le salon** → **Intégrations** → **Webhooks** → **Nouveau webhook** → **Copier l'URL du webhook**.
3. Sur GitHub : **Settings → Secrets and variables → Actions → New repository secret** : nom `DISCORD_WEBHOOK`, valeur = l'URL copiée.

### E-mail (Gmail)
1. Le compte Google doit avoir la validation en deux étapes. Créez un **mot de passe d'application** sur https://myaccount.google.com/apppasswords.
2. Trois secrets GitHub : `SMTP_USER` (votre adresse Gmail), `SMTP_PASSWORD` (le mot de passe d'application, pas votre mot de passe habituel), `ALERT_EMAIL` (l'adresse qui reçoit les alertes).

Vous pouvez activer Discord, l'e-mail, ou les deux.

## Ce qu'il faut savoir
- **Les premiers jours** : les ventes par jour apparaissent après quelques heures, les médianes 7 jours et les mouvements de la semaine deviennent fiables après une semaine, les meilleurs jours d'achat/vente après 14 jours.
- **Ventes estimées** : l'API ne donne pas les ventes. On compare deux photos successives : une annonce qui disparaît alors qu'il lui restait plus de 2 h a probablement été achetée (ou annulée par le vendeur). C'est une estimation.
- **Votre liste suivie** est enregistrée dans votre navigateur : elle n'apparaît pas sur un autre appareil.
- **Ajouter des recettes** : modifiez `recipes.json`. Les numéros d'objets se trouvent dans l'adresse de la fiche objet sur les sites de base de données, ou en cliquant sur l'objet dans le tableau de bord. Pour un composant acheté au PNJ, renseignez `"vendor_price"` en cuivre (1po = 10000).
- **Taxe HV** : 5 % par défaut (`ah_cut` dans `config.json`).
- **Valeur de marché** : moyenne des 15 % d'unités les moins chères, sans les prix aberrants. Elle sert à tous les calculs (médiane 7 jours, variations, métiers) ; le « plus bas » reste affiché car c'est le prix auquel on achète vraiment.
- **Mise en veille GitHub** : GitHub peut désactiver une tâche planifiée après 60 jours sans activité sur le dépôt. Vous recevez un e-mail avant : il suffit de cliquer sur « Enable workflow ».
- **Si Blizzard change son API** (namespace, numéros d'HV), l'étape « Collecte » de l'onglet Actions affichera une erreur : c'est le premier endroit à regarder.

## Fichiers
| Fichier | Rôle |
|---|---|
| `collector.py` | Télécharge l'HV, calcule les prix, estime les ventes |
| `alerts.py` | Alertes Discord / e-mail |
| `recipes_builder.py` | Recettes de tous les métiers, tirées des fichiers du jeu |
| `alerts.json` | Vos prix cibles d'alerte |
| `farms.json` | Routes de farm et rendements par heure |
| `config.json` | Région, langue, numéros d'HV, taxe |
| `recipes.json` | Vos recettes de métiers |
| `watchlist.json` | Objets suivis par défaut au premier lancement (ensuite, la liste se gère dans le tableau de bord) |
| `web/index.html` | Le tableau de bord |
| `.github/workflows/collect.yml` | La tâche horaire sur GitHub |

L'historique est conservé dans la branche `data` du dépôt (90 jours).
Données : API Battle.net de Blizzard. Outil non officiel.
