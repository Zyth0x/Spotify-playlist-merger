# Spotify Playlist Merger

Application web locale qui fusionne plusieurs playlists Spotify en une seule, sans jamais voir votre mot de passe Spotify.

L'application tourne uniquement sur votre machine (`127.0.0.1:8888`) et utilise OAuth 2.0 avec PKCE : aucun secret client n'est nécessaire et les jetons restent stockés en local.

## Fonctionnalités

- Fusion de plusieurs playlists vers une **nouvelle playlist** ou une **playlist existante**
- Déduplication par URI, avec option par ISRC (même morceau dans des éditions différentes)
- Mode **simulation** (dry run) pour prévisualiser le résultat sans rien modifier
- Progression en temps réel, statistiques détaillées et annulation possible
- Exécution locale : aucun serveur distant, aucune donnée envoyée ailleurs qu'à l'API Spotify

## Prérequis

- Python 3.8 ou supérieur
- Un compte Spotify (**Premium** requis par Spotify pour les applications en mode développement)

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

## Configuration

1. Ouvrez le [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) et créez une application.
2. Dans les paramètres de l'application, ajoutez la Redirect URI suivante, exactement :

   ```
   http://127.0.0.1:8888/callback
   ```

3. Copiez le **Client ID** et collez-le dans l'application au premier lancement.
4. Cliquez sur « Se connecter avec Spotify » et autorisez l'accès.

## Utilisation

1. Sélectionnez une ou plusieurs playlists sources.
2. Choisissez la destination : nouvelle playlist (avec nom, visibilité, description) ou playlist existante.
3. Réglez les options de déduplication et, si besoin, activez le mode simulation.
4. Lancez la fusion et suivez la progression.

## Structure du projet

```
app.py               Serveur Flask et logique de fusion
spotify_client.py    Client Spotify (OAuth PKCE, API REST, pagination, throttling)
templates/           Interface web
static/              JavaScript et styles
data/                Configuration et jetons (créé au premier lancement, non versionné)
run.sh               Script de démarrage
```

## Données locales et sécurité

- `data/config.json` contient uniquement le Client ID.
- `data/tokens.json` contient les jetons OAuth, avec des permissions de fichier restreintes (`0600`).
- Aucun mot de passe Spotify ne transite par cette application. La déconnexion supprime les jetons locaux.

## Limites connues

- Spotify limite les playlists à **10 000 morceaux**.
- Seules vos playlists et celles en collaboration peuvent être modifiées ou lues intégralement.
- Les fichiers locaux et les épisodes de podcast sont ignorés lors de la fusion.
- L'interface est en français.

## Licence

[MIT](LICENSE)
