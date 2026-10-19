# Spotify Playlist Merger

Application web locale qui fusionne plusieurs playlists Spotify en une seule et peut aussi les **transférer vers Apple Music** (ou vers une playlist Apple Music existante), sans jamais voir vos mots de passe.

L'application tourne uniquement sur votre machine (`127.0.0.1:8888`). Spotify utilise OAuth 2.0 avec PKCE : aucun secret client n'est nécessaire. Apple Music utilise un jeton développeur MusicKit signé localement et un jeton utilisateur obtenu dans le navigateur.

## Fonctionnalités

- Fusion de plusieurs playlists vers une **nouvelle playlist Spotify** ou une **playlist Spotify existante**
- **Transfert Spotify → Apple Music** : nouvelle playlist ou ajout à une playlist de la bibliothèque Apple Music
- Correspondance des morceaux par **ISRC** en priorité, puis par recherche titre/artiste avec normalisation (feat., remaster, accents, ponctuation…)
- Déduplication par URI Spotify, avec option par ISRC
- Mode **simulation** (dry run) pour prévisualiser le résultat sans rien modifier
- Progression en temps réel, statistiques détaillées, liste des morceaux introuvables et annulation possible
- Exécution locale : aucune donnée envoyée ailleurs qu'aux API Spotify et Apple Music

## Prérequis

- Python 3.8 ou supérieur
- Un compte Spotify (**Premium** requis par Spotify pour les applications en mode développement)
- Pour Apple Music :
  - un abonnement **Apple Music** actif ;
  - un compte **Apple Developer Program** (payant) pour créer une clé MusicKit et signer le jeton développeur.

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

## Utilisation

1. Sélectionnez une ou plusieurs playlists sources Spotify.
2. Choisissez la destination :
   - **Spotify** : nouvelle playlist ou playlist existante ;
   - **Apple Music** : nouvelle playlist ou playlist existante de votre bibliothèque.
3. Réglez les options de déduplication et, si besoin, activez le mode simulation.
4. Lancez la fusion ou le transfert et suivez la progression.

En mode Apple Music, chaque morceau est d'abord recherché par ISRC dans le catalogue Apple Music.
S'il n'est pas trouvé, une recherche par titre et artiste est effectuée ; les résultats sont départagés
par la ressemblance du titre, de l'artiste et de la durée. Les morceaux non trouvés sont ignorés et
listés à la fin. Le mode simulation est recommandé avant le premier transfert.

## Tests

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

Les tests couvrent la normalisation et le rapprochement des morceaux, la logique de transfert
(création, déduplication, simulation, lots de 100 morceaux) et la configuration Apple Music.
Les appels réels aux API Spotify et Apple Music ne sont pas effectués dans les tests.

## Structure du projet

```
app.py               Serveur Flask, logique de fusion et de transfert
spotify_client.py    Client Spotify (OAuth PKCE, API REST, pagination, throttling)
apple_client.py      Client Apple Music (jeton MusicKit, catalogue, bibliothèque, playlists)
matching.py          Normalisation et rapprochement des morceaux entre catalogues
templates/           Interface web
static/              JavaScript et styles
tests/               Tests unitaires (rapprochement, transfert, configuration)
data/                Configuration, clés et jetons locaux (non versionnés)
run.sh               Script de démarrage
```

## Données locales et sécurité

- `data/config.json` contient le Client ID Spotify et les identifiants non secrets Apple Music (Team ID, Key ID, storefront).
- `data/tokens.json` contient les jetons OAuth Spotify, avec des permissions de fichier restreintes (`0600`).
- `data/apple_key.p8` contient la clé privée MusicKit, avec des permissions restreintes (`0600`).
- `data/apple_tokens.json` contient le jeton utilisateur Apple Music, avec des permissions restreintes (`0600`).
- Aucun mot de passe ne transite par cette application. La déconnexion supprime les jetons locaux.
- Le jeton développeur Apple est signé localement ; il n'est jamais stocké sur un serveur distant.

## Limites connues

- Spotify limite les playlists à **10 000 morceaux**.
- Seules vos playlists Spotify et celles en collaboration peuvent être modifiées ou lues intégralement.
- Apple Music exige un compte Apple Developer Program et un abonnement actif.
- La disponibilité des morceaux sur Apple Music dépend du storefront choisi.
- La correspondance automatique peut produire quelques faux positifs (versions live, remix) ou faux négatifs ;
  le mode simulation et la liste des morceaux introuvables permettent de contrôler le résultat.
- Les fichiers locaux et les épisodes de podcast sont ignorés.
- Le jeton utilisateur Apple Music peut expirer ou être révoqué ; il faut alors se reconnecter.
- L'interface est en français.

## Ajouter un autre fournisseur

La logique de transfert est séparée en deux étapes : collecte des morceaux Spotify, puis résolution et
écriture côté destination. Pour ajouter un fournisseur, il suffit de fournir un client avec les opérations
`search_song_by_isrc`, `search_songs`, `create_playlist`, `add_tracks` et, si besoin, la lecture des
playlists existantes, puis de brancher ce client dans `app.py` comme c'est fait pour Apple Music.

## Licence

[MIT](LICENSE)
