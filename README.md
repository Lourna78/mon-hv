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

## Ce qu'il faut savoir
- **Les premiers jours** : les ventes par jour apparaissent après quelques heures, les médianes 7 jours et les mouvements de la semaine deviennent fiables après une semaine, les meilleurs jours d'achat/vente après 14 jours.
- **Ventes estimées** : l'API ne donne pas les ventes. On compare deux photos successives : une annonce qui disparaît alors qu'il lui restait plus de 2 h a probablement été achetée (ou annulée par le vendeur). C'est une estimation.
- **Votre liste suivie** est enregistrée dans votre navigateur : elle n'apparaît pas sur un autre appareil.
- **Ajouter des recettes** : modifiez `recipes.json`. Les numéros d'objets se trouvent dans l'adresse de la fiche objet sur les sites de base de données, ou en cliquant sur l'objet dans le tableau de bord. Pour un composant acheté au PNJ, renseignez `"vendor_price"` en cuivre (1po = 10000).
- **Taxe HV** : 5 % par défaut (`ah_cut` dans `config.json`).
- **Mise en veille GitHub** : GitHub peut désactiver une tâche planifiée après 60 jours sans activité sur le dépôt. Vous recevez un e-mail avant : il suffit de cliquer sur « Enable workflow ».
- **Si Blizzard change son API** (namespace, numéros d'HV), l'étape « Collecte » de l'onglet Actions affichera une erreur : c'est le premier endroit à regarder.

## Fichiers
| Fichier | Rôle |
|---|---|
| `collector.py` | Télécharge l'HV, calcule les prix, estime les ventes |
| `config.json` | Région, langue, numéros d'HV, taxe |
| `recipes.json` | Vos recettes de métiers |
| `watchlist.json` | Objets suivis par défaut au premier lancement (ensuite, la liste se gère dans le tableau de bord) |
| `web/index.html` | Le tableau de bord |
| `.github/workflows/collect.yml` | La tâche horaire sur GitHub |

L'historique est conservé dans la branche `data` du dépôt (90 jours).
Données : API Battle.net de Blizzard. Outil non officiel.
