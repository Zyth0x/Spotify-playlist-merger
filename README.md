# Spotify Playlist Merger

Application web locale qui fusionne plusieurs playlists Spotify en une seule et peut aussi les **transférer vers Apple Music, Deezer ou YouTube Music**, sans jamais voir vos mots de passe.

L'application tourne uniquement sur votre machine (`127.0.0.1:8888`). Spotify utilise OAuth 2.0 avec PKCE, Apple Music utilise MusicKit, Deezer utilise OAuth 2.0 et YouTube Music s'appuie sur `ytmusicapi`. Tous les jetons restent stockés localement.

## Fonctionnalités

- Fusion de plusieurs playlists vers une **nouvelle playlist Spotify** ou une **playlist Spotify existante**
- **Transfert Spotify → Apple Music**, **Spotify → Deezer** et **Spotify → YouTube Music**
- Création d'une nouvelle playlist ou ajout à une playlist existante du fournisseur choisi
- Correspondance des morceaux par **ISRC** sur Apple Music et Deezer, puis par recherche titre/artiste avec normalisation (feat., remaster, accents, ponctuation…)
- Correspondance par titre, artiste et durée sur YouTube Music (pas d'ISRC public)
- Déduplication par URI Spotify, avec option par ISRC
- Mode **simulation** (dry run) pour prévisualiser le résultat sans rien modifier
- Progression en temps réel, statistiques détaillées, liste des morceaux introuvables et annulation possible
- Exécution locale : aucune donnée envoyée ailleurs qu'aux API des fournisseurs choisis

## Fournisseurs pris en charge

| Fournisseur | Destination | Authentification |
| --- | --- | --- |
| Spotify | Oui | OAuth 2.0 + PKCE |
| Apple Music | Oui | MusicKit + clé `.p8` |
| Deezer | Oui | OAuth 2.0 |
| YouTube Music | Oui | OAuth Google via `ytmusicapi` |

La source des playlists reste Spotify : l'application lit vos playlists Spotify, puis les recrée sur le fournisseur de destination sélectionné.

## Prérequis

- Python 3.9 ou supérieur
- Un compte Spotify (**Premium** requis par Spotify pour les applications en mode développement)
- Pour Apple Music :
  - un abonnement **Apple Music** actif ;
  - un compte **Apple Developer Program** (payant) pour créer une clé MusicKit et signer le jeton développeur.
- Pour Deezer : un compte Deezer et une application créée sur le portail développeur (gratuit).
- Pour YouTube Music :
  - un compte YouTube Music (Google) ;
  - un projet Google Cloud avec l'API **YouTube Data v3** activée et un client OAuth de type **« TV et appareils à accès limité »**.

## Installation

```bash
./run.sh
```

Le script crée l'environnement virtuel `.venv`, installe les dépendances, démarre le serveur et ouvre `http://127.0.0.1:8888` dans le navigateur.

Lancement manuel :

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Configuration Spotify

1. Ouvrez le [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) et créez une application.
2. Dans les paramètres de l'application, ajoutez la Redirect URI suivante, exactement :

   ```
   http://127.0.0.1:8888/callback
   ```

3. Copiez le **Client ID** et collez-le dans l'application au premier lancement.
4. Cliquez sur « Se connecter avec Spotify » et autorisez l'accès.

## Configuration Apple Music

1. Dans le [portail Apple Developer](https://developer.apple.com/account/resources/), ouvrez
   **Certificates, Identifiers & Profiles → Keys**, puis créez une clé avec le service **MusicKit** activé.
2. Notez le **Key ID** de la clé, et le **Team ID** affiché dans les informations de votre compte.
3. Téléchargez la clé privée `.p8` (Apple ne permet de la télécharger qu'une fois).
4. Dans l'application, choisissez la destination **Apple Music**, puis renseignez le Team ID,
   le Key ID, le storefront (`fr` par défaut) et sélectionnez le fichier `.p8`.
5. Cliquez sur **Se connecter à Apple Music** et autorisez l'application dans la fenêtre Apple.

Le jeton développeur (JWT ES256) est généré localement et signé avec votre clé. Le jeton utilisateur
MusicKit est stocké localement dans `data/apple_tokens.json`. Il peut expirer : il suffit alors de
recliquer sur « Se connecter à Apple Music ».

## Configuration Deezer

1. Ouvrez le [portail développeur Deezer](https://developers.deezer.com/) et créez une application.
2. Déclarez la Redirect URI suivante :

   ```
   http://127.0.0.1:8888/deezer/callback
   ```

3. Copiez l'**App ID** et l'**App Secret** dans l'application (destination Deezer).
4. Cliquez sur **Se connecter à Deezer** et autorisez l'accès.

Les droits demandés sont `basic_access`, `manage_library` et `offline_access`. Le jeton Deezer est
stocké dans `data/deezer_tokens.json`.

## Configuration YouTube Music

YouTube Music ne dispose pas d'API officielle de gestion des playlists. L'intégration utilise
[`ytmusicapi`](https://github.com/sigma67/ytmusicapi) et l'authentification OAuth Google réservée aux
appareils à accès limité.

1. Dans la [Google Cloud Console](https://console.cloud.google.com/), créez un projet (ou utilisez un projet existant).
2. Activez l'**API YouTube Data v3**.
3. Dans **APIs & Services → Credentials**, créez un **ID client OAuth** de type
   **« TV et appareils à accès limité »**.
4. Copiez le **Client ID** et le **Client Secret** dans l'application (destination YouTube Music).
5. Cliquez sur **Connecter YouTube Music**. L'application affiche un code et ouvre la page
   `google.com/device` : saisissez le code avec le compte Google lié à votre YouTube Music.
6. Une fois l'autorisation accordée, la connexion est détectée automatiquement.

Si l'écran de consentement Google est encore en mode « test », ajoutez votre compte dans la liste
des utilisateurs de test du projet Google Cloud.

Le jeton OAuth est stocké dans `data/ytmusic_oauth.json` et rafraîchi automatiquement par `ytmusicapi`.
Cette intégration dépend d'une API non officielle : elle peut cesser de fonctionner si Google modifie
son service.

## Utilisation

1. Sélectionnez une ou plusieurs playlists sources Spotify.
2. Choisissez la destination : **Spotify**, **Apple Music**, **Deezer** ou **YouTube Music**.
3. Pour le fournisseur choisi, créez une nouvelle playlist ou sélectionnez une playlist existante.
4. Réglez les options de déduplication et, si besoin, activez le mode simulation.
5. Lancez la fusion ou le transfert et suivez la progression.

En mode externe, chaque morceau est recherché dans le catalogue du fournisseur. Sur Apple Music et
Deezer, l'ISRC est utilisé en priorité, puis une recherche par titre et artiste. Sur YouTube Music,
la recherche se fait par titre, artiste et durée. Les morceaux non trouvés sont ignorés et listés
à la fin. Le mode simulation est recommandé avant le premier transfert.

## Tests

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

Les tests couvrent la normalisation et le rapprochement des morceaux, la logique de transfert
(création, déduplication, simulation, lots de 100 morceaux), les adaptateurs Apple Music, Deezer et
YouTube Music, ainsi que la configuration locale. Les appels réels aux API ne sont pas effectués
dans les tests.

## Structure du projet

```
app.py               Serveur Flask, logique de fusion et de transfert
spotify_client.py    Client Spotify (OAuth PKCE, API REST, pagination, throttling)
apple_client.py      Client Apple Music (MusicKit, catalogue, bibliothèque)
deezer_client.py     Client Deezer (OAuth, catalogue, playlists)
youtube_client.py    Client YouTube Music (ytmusicapi, OAuth Google)
providers.py         Adaptateurs de destination communs aux fournisseurs externes
matching.py          Normalisation et rapprochement des morceaux entre catalogues
templates/           Interface web
static/              JavaScript et styles
tests/               Tests unitaires
data/                Configuration, clés et jetons locaux (non versionnés)
run.sh               Script de démarrage
```

## Données locales et sécurité

- `data/config.json` contient le Client ID Spotify, les identifiants non secrets Apple Music et les
  identifiants d'application Deezer / Google (y compris les secrets, avec permissions `0600`).
- `data/tokens.json` contient les jetons OAuth Spotify, avec des permissions restreintes (`0600`).
- `data/apple_key.p8` contient la clé privée MusicKit (`0600`).
- `data/apple_tokens.json` contient le jeton utilisateur Apple Music (`0600`).
- `data/deezer_tokens.json` contient le jeton Deezer (`0600`).
- `data/ytmusic_oauth.json` contient le jeton OAuth YouTube Music (`0600`).
- Aucun mot de passe ne transite par cette application. La déconnexion supprime les jetons locaux.
- Le jeton développeur Apple est signé localement ; il n'est jamais stocké sur un serveur distant.

## Limites connues

- Spotify limite les playlists à **10 000 morceaux**.
- Seules vos playlists Spotify et celles en collaboration peuvent être modifiées ou lues intégralement.
- Apple Music exige un compte Apple Developer Program et un abonnement actif.
- Deezer exige une application développeur et un compte connecté.
- YouTube Music utilise une API non officielle et peut être affecté par des changements côté Google.
- La disponibilité des morceaux dépend du catalogue et du storefront de chaque fournisseur.
- La correspondance automatique peut produire quelques faux positifs (versions live, remix) ou faux
  négatifs ; le mode simulation et la liste des morceaux introuvables permettent de contrôler le résultat.
- Les fichiers locaux et les épisodes de podcast sont ignorés.
- Les jetons utilisateur peuvent expirer ou être révoqués ; il faut alors se reconnecter.
- **Amazon Music** et **Tidal** ne sont pas intégrés : leurs API de gestion de playlists nécessitent
  un accès partenaire ou une approbation spécifique.
- L'interface est en français.

## Ajouter un autre fournisseur

Les destinations externes partagent l'interface `DestinationAdapter` de `providers.py` :
`existing_tracks`, `resolve_track`, `create_playlist`, `add_tracks` et `playlist_url`. Pour ajouter
un fournisseur, implémentez cette interface, ajoutez les routes de configuration/authentification
correspondantes dans `app.py`, puis déclarez le fournisseur dans `TARGET_PROVIDERS` et dans l'interface.

## Licence

[MIT](LICENSE)
