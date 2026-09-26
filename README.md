# 🏃 Garmin Running Dashboard

Tableau de bord pour l'analyse de tes données de course à pied.
Se connecte à **Garmin Connect** (lib non officielle `garminconnect`), prépare un
**plan course + renforcement** vers ta prochaine course (envoyé à ta montre sur
confirmation), repère ce que les graphiques cachent (**dérive cardiaque**, **FC
optique calée sur la cadence**), génère des **parcours inédits** via
OpenRouteService, et se branche sur **Claude** (Claude Desktop / Claude Code, via
MCP) pour discuter de ton entraînement.

🔒 **Auto-hébergé** : tes données de santé sont stockées chez toi (cache, plan,
jetons). Quelques flux partent quand même vers des tiers, listés dans
[« Données et services tiers »](#-données-et-services-tiers) — lis-la avant d'héberger
l'outil pour quelqu'un d'autre.

> Garmin®, Garmin Connect™ et Run Coach sont des marques de Garmin Ltd. ou de ses
> filiales ; Strava est une marque de Strava, Inc. Ce projet n'est **ni affilié, ni
> approuvé** par Garmin ou Strava.

Portage Garmin du projet `run` (ex-Strava) : même architecture (Streamlit multipage,
logique pure testée, cache à deux niveaux, Docker + Caddy), mais **mono-utilisateur** —
l'API Garmin non officielle s'authentifie par identifiants, pas par OAuth multi-comptes.

---

## Fonctionnalités

- **Signaux cachés** — **veille santé** (FC de repos, HRV, respiration et SpO2
  de la nuit comparées à ta norme des 30 derniers jours : plusieurs signaux qui
  dévient ensemble précèdent souvent un rhume de 1 à 2 jours), **pic de sortie**
  (une sortie > +10 % de ta plus longue du mois, prévue ou faite), **forme de
  foulée à allure égale** (contact au sol, ratio vertical, foulée, puissance
  corrigés de la vitesse), **chaleur** (météo de chaque sortie et allure
  équivalente au frais)
- **Jour de course** — importe le GPX du parcours : allure kilomètre par
  kilomètre à effort égal (coût énergétique de la pente), objectif corrigé de la
  chaleur prévue, ravitaillement placé au kilomètre, bracelet d'allure en CSV
- **Navigation en 4 pôles** — Aujourd'hui / Entraînement / Progrès / Objectif,
  une question par pôle ; en-tête commun (Light/Pro, Actualiser, Compte,
  Prompt coach IA) et, sur téléphone, barre d'onglets en bas. Thème clair
  « Piste claire » (papier, encre, accent volt), polices servies localement
- **Accueil** — cockpit du jour : **séance du jour** (Run Coach, sinon plan
  Objectif validé, sinon calculée — la même que sur Prochaine sortie) avec son
  pourquoi, jauge de fraîcheur, récupération de la nuit (sommeil, HRV, Body
  Battery, FC repos), la semaine en 7 cases (fait / prévu), des **signaux** tirés
  des données (charge ACWR, monotonie, efficacité aérobie sur 90 j, usure des
  chaussures) et la dernière sortie en résumé
- **Activités** — un **explorateur** : choisis un indicateur (intensité en % de
  ton allure seuil, FC, allure, charge, calories, distance, cadence, dénivelé),
  chaque sortie est un point coloré par zone d'intensité, **clique-le** pour
  l'ouvrir ; la **répartition de l'intensité** semaine par semaine (le repère
  80/20 facile/dur, et l'alerte « zone grise ») ; la liste enrichie (barres
  d'intensité, de distance, de charge). Détail complet d'une sortie avec
  laps, streams (altitude / allure / FC), carte GPS, et **qualité du signal** :
  détection de la FC optique calée sur la cadence (faux relevés du capteur au
  poignet) et **dérive cardiaque** (Pa:HR) de la sortie
- **Statistiques** — 5 onglets : volume, allure, FC (zones réelles du profil Garmin),
  cadence, régularité
- **Forme & Récup** — charge d'entraînement (CTL/ATL/TSB) calculée sur **toutes
  les activités** (course à l'allure, wing / vélo / natation / renfo via la charge
  Garmin), croisée avec la récupération (HRV vs baseline, sommeil, Body Battery,
  FC repos, stress) et **verdict du jour** (prêt à performer / normal / lève le
  pied)
- **Progression** — prédictions de course (5 km, 10 km, semi, marathon) en 4
  graphes ou superposées en % de progression, avec une **projection à 30 et 90
  jours** si tu continues comme ces 8 dernières semaines ; **VO2max** et sa
  projection de 1 à 6 mois (tendance robuste, gains amortis, plafonnée,
  bande d'incertitude — une estimation, pas une promesse) ; records personnels,
  **efficacité aérobie** (vitesse ÷ FC, tendance) et dérive des sorties longues
- **Prochaine sortie** — la séance du **plan Garmin Run Coach** (nom, cibles,
  durée, phase du plan, programme de la semaine avec renfo et repos imposés), un
  avertissement quand le coach programme une séance intense sur une récupération
  dégradée, + parcours en boucle généré sur OpenStreetMap et export GPX. Sans plan
  actif, repli sur la recommandation calculée depuis la charge (TSB) et **modulée
  par la récupération** (HRV/sommeil dégradés → séance rétrogradée)
- **Heatmap** — cartes de chaleur des courses (fréquence, allure, FC, pente,
  dénivelé signé) sur fond sombre Esri
- **IA Coach** — deux prompts prêts à coller dans n'importe quel LLM :
  **analyse d'entraînement** (sorties, charge, HRV, sommeil, records, prédictions)
  et **idées de repas** (séances des prochains jours issues du plan Garmin,
  dépense énergétique sur 7 jours, contraintes alimentaires saisies)
- **Objectif** — une course datée (5 km → marathon, temps visé optionnel) →
  plan périodisé **course + renforcement** (base, développement, spécifique,
  affûtage), allures tirées de ta forme récente, chaque séance avec son
  **pourquoi** et ses sources. Une fois validé, le plan est figé ; les séances des
  14 prochains jours s'envoient dans le **calendrier Garmin** (sur confirmation,
  retirables). Si un plan Garmin Run Coach est actif, il reste la référence et
  l'envoi est bloqué
- **Modes Light / Pro** (en-tête) — Light explique les indicateurs techniques (TSB,
  HRV, dérive, VO2max…) et pourquoi il compte ; Pro affiche les chiffres bruts
  (ACWR, monotonie de Foster — l'Accueil en tire des signaux en clair dans les deux
  modes) et permet de régler les seuils des analyses
- **Comparatif annuel** — l'année en cours superposée aux précédentes sur un axe
  « jour de l'année » : charge (CTL/ATL/TSB), volume cumulé (km, D+, heures),
  physiologie (VO2max, FC repos, prédictions) et récupération (sommeil, HRV),
  avec un bandeau « à la même date » qui chiffre l'écart avec l'an dernier et un
  face-à-face de **la sortie du jour** avec celle de la même date les autres années

### Différences vs la version Strava

| | Strava (`run`) | Garmin (`gar`) |
|---|---|---|
| Authentification | OAuth multi-user, token en session | Identifiants + tokens persistés (~1 an), mono-user |
| Segments / KOM | ✅ page Segments | ❌ pas d'équivalent API Garmin |
| Prédictions de course | Formule de Riegel | **Natives Garmin** + Riegel |
| Zones FC | Configurées dans Strava | Réelles du profil Garmin (par sport) |
| Santé (sommeil, Body Battery, HRV) | ❌ | ✅ page Forme & Récup |
| Cadence | RPM à doubler | Directement en pas/min |
| Calories | Souvent absentes (estimation) | Fiables (montre) |

> ⚠️ **API non officielle** : `garminconnect` s'appuie sur les endpoints internes de
> Garmin Connect. Une mise à jour côté Garmin peut casser temporairement la lib —
> mettre à jour `garminconnect` règle généralement le problème.

---

## ⚠️ À savoir avant d'installer

Ce dashboard est un projet personnel, conçu pour tourner **en local, pour un seul
utilisateur**. Quelques points à connaître avant de l'installer :

- **La bibliothèque `garminconnect` n'est pas officielle.** Elle rejoue l'API
  interne de Garmin Connect. Garmin peut la casser sans préavis, renvoyer des
  `429 Too Many Requests` en cas d'appels trop fréquents, et rien ne garantit
  contractuellement que l'usage soit toléré : les conditions d'utilisation de
  Garmin interdisent l'accès automatisé hors de leurs API officielles, et un compte
  peut être bloqué. Tu utilises tes propres identifiants, à tes risques.
- **Ton mot de passe Garmin n'a pas besoin d'être dans `.env`** : le dashboard le
  demande dans son formulaire et ne lit jamais `GARMIN_PASSWORD` ;
  `test_connection.py` le demande aussi s'il est absent. Si tu l'y mets quand
  même, il est en clair : garde `.env` hors de tout dépôt (il est dans
  `.gitignore`). Ensuite, ce sont les tokens qui servent (~1 an).
- **L'application n'a aucune authentification propre** : quiconque atteint le port
  voit tes données Garmin. C'est pourquoi la stack de dev publie le port sur
  `127.0.0.1:8501` uniquement — le dashboard n'est joignable que **depuis la machine
  qui le fait tourner**, pas depuis le reste du réseau local. Voir
  [Accès depuis un autre appareil](#4-optionnel--accès-depuis-un-autre-appareil)
  pour ouvrir cet accès en connaissance de cause.
- **Chacun installe sa propre instance.** L'app est mono-utilisateur : elle ne sait
  pas gérer plusieurs comptes Garmin en parallèle.

---

## Prérequis

| Outil | Version minimale | Vérification |
|---|---|---|
| [Docker](https://docs.docker.com/get-docker/) | 24.x | `docker --version` |
| [Docker Compose](https://docs.docker.com/compose/install/) | v2.x | `docker compose version` |
| Compte Garmin Connect | — | [connect.garmin.com](https://connect.garmin.com) |
| Clé API OpenRouteService | — | [openrouteservice.org](https://openrouteservice.org/dev/#/signup) *(gratuit, optionnel)* |
| Python | 3.12 | `python3 --version` *(uniquement pour lancer les tests hors Docker)* |

---

## Démarrage rapide (dev local)

### 1. Configurer l'environnement

```bash
cp .env.example .env
```

Éditez le fichier `.env` :

```dotenv
GARMIN_EMAIL=ton.email@exemple.com
GARMIN_PASSWORD=ton_mot_de_passe
CACHE_TTL=3600

# Optionnel — page Prochaine sortie
ORS_API_KEY=ta_cle_ors
```

> ⚠️ **Sécurité :** le fichier `.env` ne doit jamais être versionné (il est dans
> `.gitignore`). Les identifiants ne servent qu'au premier login : ensuite ce sont
> les tokens (volume `.garmin/`) qui sont utilisés.

### 2. Démarrer

```bash
docker compose up -d
```

Streamlit démarre avec hot-reload sur **[http://localhost:8501](http://localhost:8501)**,
accessible uniquement depuis cette machine.

### 3. Se connecter à Garmin

Ouvre **[http://localhost:8501](http://localhost:8501)**, saisis ton mot de passe Garmin (l'email est pré-rempli si `GARMIN_EMAIL` est
dans `.env`) et clique sur **« Se connecter à Garmin »**. Le dashboard n'utilise
jamais `GARMIN_PASSWORD` : les tokens obtenus tiennent ~1 an. Si ton compte a le MFA activé, un champ apparaît
pour saisir le code reçu par email.

Les tokens sont ensuite persistés dans `app/.garmin/` (dev) ou le volume
`garmin_tokens` (prod) : les démarrages suivants se connectent **automatiquement**,
sans mot de passe ni MFA, pendant environ un an.

### 4. (Optionnel) Accès depuis un autre appareil

Par défaut, `docker-compose.yml` publie le port sur la seule interface de loopback :

```yaml
ports:
  - "127.0.0.1:8501:8501"   # local uniquement
```

Pour ouvrir le dashboard au **réseau local** (consulter depuis ton téléphone, par
exemple), remplace cette ligne par `- "8501:8501"`, ajoute l'IP de la machine à la
liste des hôtes acceptés (protection contre le DNS rebinding, sinon la page reste
bloquée au chargement) puis relance `docker compose up -d` :

```yaml
environment:
  STREAMLIT_SERVER_ALLOWED_HOSTS: "localhost,127.0.0.1,192.168.1.20"   # ← ton IP
```
 Mesure les conséquences avant : l'app n'ayant aucune
authentification, **tout appareil du réseau** — y compris sur un wifi partagé,
au bureau ou en coworking — pourra lire l'intégralité de tes données Garmin en
ouvrant `http://<ip-de-la-machine>:8501`.

> ⚠️ Un pare-feu ne suffit pas à corriger ça après coup : Docker insère ses
> propres règles iptables et **contourne UFW**, donc un `ufw deny 8501` ne bloque
> pas un port publié sur `0.0.0.0`. C'est bien l'adresse de publication qui
> protège.

Pour une exposition sur **Internet**, n'utilise pas cette stack : passe par
`docker-compose.prod.yml` (Caddy + HTTPS, port 8501 non publié) et ajoute une
protection d'accès — l'authentification basique de Caddy, un tunnel VPN
(WireGuard, Tailscale) ou un proxy d'identité.

---

## Déploiement en production

Identique au projet `run` : Caddy en frontal HTTPS (Let's Encrypt auto).

```bash
# .env : compléter PUBLIC_DOMAIN, ACME_EMAIL, STREAMLIT_BROWSER_SERVER_ADDRESS,
#        BASIC_AUTH_USER et BASIC_AUTH_HASH (entre quotes simples)
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'mon-mot-de-passe'
docker compose -f docker-compose.prod.yml up -d --build
```

🔒 L'app n'a pas d'authentification propre et reprend la session Garmin du
serveur : **Caddy impose une authentification basique** (`BASIC_AUTH_USER` /
`BASIC_AUTH_HASH`). Sans ces variables, `docker compose` refuse de démarrer —
c'est voulu. Un VPN (WireGuard, Tailscale) reste une bonne protection en plus.

---

## Commandes utiles

```bash
# ── Dev ──────────────────────────────────────────────────────────────
docker compose up -d                # démarrer
docker compose down                 # arrêter
docker compose logs -f              # logs temps réel
docker compose exec app rm -rf /app/.cache   # vider le cache disque

# ── Tests (hors Docker, venv local) ──────────────────────────────────
# Créer le venv une fois :
python3 -m venv .venv
.venv/bin/pip install -r app/requirements.txt -r requirements.txt pytest playwright
.venv/bin/playwright install chromium   # optionnel : sinon le Chrome du système sert

# Puis — trois suites, à lancer séparément (tests/ remplace streamlit par un mock) :
.venv/bin/python -m pytest tests/ -v          # logique pure
.venv/bin/python -m pytest tests_ui/ -q       # pages rendues en headless (AppTest, faux Garmin)
.venv/bin/python -m pytest tests_e2e/ -q      # vrai navigateur : clics, CSS, mobile (Playwright)

# ── Test de connexion CLI ────────────────────────────────────────────
.venv/bin/python test_connection.py

# ── Prod ─────────────────────────────────────────────────────────────
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f app
```

Pour se déconnecter : menu **« Compte » → « Déconnexion »** dans l'en-tête
(supprime le tokenstore).

---

## Variables d'environnement

| Variable | Défaut | Description |
|---|---|---|
| `GARMIN_EMAIL` | — | Email du compte Garmin (pré-remplit le formulaire) |
| `GARMIN_PASSWORD` | — | Utilisé seulement par `test_connection.py` et le serveur MCP (jamais par le dashboard) |
| `GARMIN_TOKENSTORE` | `/app/.garmin` (Docker) ou `~/.garminconnect` | Dossier des tokens du dashboard |
| `CACHE_DIR` | `/app/.cache` (Docker) ou `~/.cache/garmin-dashboard` | Cache disque des appels API |
| `CACHE_TTL` | `3600` | Durée du cache disque en secondes |
| `STREAMS_CACHE_TTL` | `2592000` (30 j) | Durée du cache des streams d'activité (conservés par « Actualiser ») |
| `ORS_API_KEY` | — | Clé OpenRouteService (page Prochaine sortie) |
| `PUBLIC_DOMAIN` | — | *(prod)* Domaine servi par Caddy |
| `ACME_EMAIL` | — | *(prod)* Email Let's Encrypt |
| `STREAMLIT_BROWSER_SERVER_ADDRESS` | `localhost` | *(prod)* Hostname annoncé au navigateur |
| `BASIC_AUTH_USER` | — | *(prod, obligatoire)* Identifiant de l'authentification Caddy |
| `BASIC_AUTH_HASH` | — | *(prod, obligatoire)* Hash bcrypt (`caddy hash-password`), entre quotes simples |
| `GARMIN_WRITE_ENABLED` | `false` (`true` dans `docker-compose.yml`) | Autorise l'envoi de séances dans le calendrier Garmin (page Objectif). Activé en dev car le port n'écoute que sur 127.0.0.1 ; à n'activer en prod que derrière l'authentification |
| `DATA_DIR` | `/app/.data` (Docker) ou `~/.local/share/garmin-dashboard` | Objectif, plan validé et journal des séances envoyées (à sauvegarder : volume `app_data` en prod) |
| `GARMIN_TOKENSTORE_MCP` | `~/.garminconnect-mcp` | Tokens du serveur MCP (volontairement distincts de ceux du dashboard) |
| `TZ` | `Europe/Paris` (compose) | Fuseau de « aujourd'hui » (titre, séance du jour) ; le conteneur serait sinon en UTC |

---

## Structure du projet

```
gar/
├── docker-compose.yml          # Stack dev (hot-reload, port limité à 127.0.0.1)
├── docker-compose.prod.yml     # Stack prod (Caddy + Streamlit, volumes nommés)
├── Caddyfile                   # Reverse-proxy HTTPS + headers de sécurité
├── .env.example                # Template de configuration
├── pytest.ini                  # Configuration des tests (pythonpath = app)
├── test_connection.py          # Test CLI de connexion Garmin
├── .mcp.json                   # Déclaration du serveur MCP pour Claude Code
├── garmin_mcp/
│   ├── server.py                 # Serveur MCP (stdio), liste blanche de lecture
│   └── insights.py               # Outils « raisonnement » (réutilisent app/)
├── tests_ui/                   # Pages rendues en headless (AppTest + faux Garmin)
├── tests_e2e/                  # Vrai navigateur (Playwright) contre le dashboard de démo
│   ├── demo_server.py            # Lance le dashboard sur le faux Garmin (aucune donnée réelle)
│   └── test_navigation.py        # Pôles, sous-pages, Light/Pro, Actualiser, barre mobile
├── tests/
│   ├── conftest.py               # Fixtures pytest et stubs Streamlit/Plotly
│   ├── test_garmin_client.py     # Transformations Garmin, cache, erreurs
│   ├── test_forme_logic.py       # Verdict de forme, rétrogradation de séance
│   ├── test_progression_logic.py # Records, Riegel, historique prédictions
│   ├── test_comparatif_logic.py  # Alignement des années, cumuls, instantanés
│   ├── test_coach_logic.py       # Plan Garmin Run Coach, cibles, fusion reco
│   ├── test_formatting.py        # decimate, map_zoom
│   ├── test_next_session.py      # Logique TSB / recommandation / GPX / ACWR
│   ├── test_physio_logic.py      # FC calée sur la cadence, dérive, efficacité
│   ├── test_race_plan_logic.py   # Générateur de plan (phases, charge, renfo)
│   ├── test_workout_export.py    # Export Garmin, garde d'envoi, journal
│   ├── test_glossary.py          # Glossaire du mode Light
│   ├── test_mcp.py               # Outils MCP, liste blanche
│   └── test_heatmap_logic.py     # Haversine, detect_home, rasterize, normalize
└── app/
    ├── Dockerfile              # Image Docker (Python 3.12-slim, user non-root)
    ├── requirements.txt        # Dépendances Python
    ├── main.py                 # Routeur st.navigation + login Garmin (MFA)
    ├── nav.py                  # 4 pôles, en-tête, barre d'onglets mobile
    ├── ui_theme.py             # Thème « Piste claire » : CSS, cartes, jauge, semaine
    ├── home_logic.py           # Logique pure : semaine en 7 cases, signaux de l'Accueil
    ├── garmin_client.py        # Client Garmin + cache + transformations
    ├── chart_theme.py          # Palette validée + template Plotly gar
    ├── next_session_logic.py   # Logique pure : TSB, recommandation, GPX
    ├── forme_logic.py          # Logique pure : verdict forme, rétrogradation
    ├── progression_logic.py    # Logique pure : records, Riegel, prédictions
    ├── comparatif_logic.py     # Logique pure : alignement des années, cumuls
    ├── coach_logic.py          # Logique pure : plan Garmin Run Coach, cibles
    ├── physio_logic.py         # Logique pure : lock FC/cadence, dérive, efficacité
    ├── race_plan_logic.py      # Logique pure : plan vers un objectif (course + renfo)
    ├── workout_export.py       # Logique pure : séances → workouts Garmin, garde d'envoi
    ├── goal_store.py           # Objectif, plan validé, journal (JSON, DATA_DIR)
    ├── glossary.py             # Textes pédagogiques du mode Light
    ├── ui_mode.py              # Bascule Light / Pro, réglages Pro
    ├── physio_ui.py            # Rendus qualité du signal / efficacité
    ├── heatmap_logic.py        # Logique pure : rasterize, blur, normalize
    ├── formatting.py           # Logique pure : pace, types d'activité
    ├── ui_helpers.py           # require_login(), get_garmin_client(), carte
    ├── stats_tabs/             # Onglets de Statistiques (+ charge sur Forme)
    └── pages/
        ├── 0_Accueil.py        # Cockpit du jour (séance, fraîcheur, semaine, signaux)
        ├── 1_Activities.py     # Liste et détails des activités
        ├── 2_Stats.py          # Volume, allure, FC, cadence, régularité
        ├── 3_Forme.py          # Charge × récupération + verdict du jour
        ├── 4_Progression.py    # Records, prédictions, VO2max
        ├── 5_Next_Session.py   # Séance du coach Garmin + parcours ORS
        ├── 6_Heatmap.py        # Heatmaps multi-calques (Folium)
        ├── 7_AI_Coach.py       # Prompts LLM avec contexte complet
        ├── 8_Comparatif.py     # Années superposées (charge, volume, physio, récup)
        ├── 9_Objectif.py       # Objectif de course → plan → envoi au calendrier
        └── 10_Jour_de_course.py # GPX → allure au km à effort égal, chaleur, ravitaillement
```

---

## Architecture et fonctionnement

### Cache à deux niveaux

```
Requête données
      │
      ▼
@st.cache_data (RAM, TTL 1h)
      │ miss
      ▼
_cache_get(athlete_id, key) (fichier JSON sur disque, TTL 1h)
      │ miss
      ▼
API Garmin Connect (réseau, cooldown 0.4 s après chaque appel réel)
```

Les **streams** d'activité (FC, allure, cadence… point par point) ont leur
propre dossier `streams/` et un TTL long (`STREAMS_CACHE_TTL`, 30 jours) : une
sortie passée ne change plus, et la page Progression en analyse jusqu'à 12.
Le bouton « Actualiser » vide tout le reste mais **conserve les streams**
(il n'en purge que les expirés).

Le cooldown évite le ban temporaire que Garmin applique aux clients trop agressifs.

### Charge d'entraînement : toutes les activités

Le PMC (CTL / ATL / TSB) agrège **l'ensemble** des activités, via
`next_session_logic.daily_tss` :

- **course** — TSS d'allure classique : `durée × IF²  × 100`, avec
  `IF = allure_seuil / allure_moyenne` (`trail_running` et compagnie sont
  normalisés en `running`, donc déjà comptés) ;
- **tout le reste** (wing, vélo, natation, renforcement…) — la charge
  d'entraînement Garmin (`activityTrainingLoad`, dérivée de l'EPOC), convertie
  en TSS par un facteur recalibré sur les courses de l'athlète
  (`cross_training_factor` : rapport des sommes TSS / charge Garmin sur les
  courses, qui portent les deux métriques). Le facteur est borné et retombe sur
  `CROSS_TRAINING_FALLBACK_K` quand l'historique de course est trop court.

Pourquoi calibrer plutôt que sommer : les deux échelles ne sont pas les mêmes.
Sans conversion, le CTL changerait d'unité selon la part de sport croisé de la
semaine et ne serait plus comparable d'une année à l'autre. Avec, une heure de
wing pèse ce qu'elle pèse vraiment — et le dashboard n'annonce plus « bien
reposé » au lendemain de la plus grosse séance de la semaine parce que celle-ci
n'était pas une course.

Conséquences pratiques :

- les pages passent l'historique **complet** aux fonctions de charge
  (`compute_tsb(df)`, `compute_pmc_series(df, …)`), et non un DataFrame filtré
  sur `activityType == "running"` ;
- `recommend_session(running_df, load_df=df)` garde les courses pour calibrer
  les cibles de la séance (distance, allure, D+ habituels) et lit le TSB global
  sur `load_df` ;
- `reference_threshold_sec` ne regarde que la course, même quand on lui passe
  tout : une sortie vélo de 8 km n'a rien à dire sur l'allure seuil ;
- le bloc charge de la page Forme empile les deux sources de TSS (course /
  autres sports) et affiche la part hors course sur la période.

### Profondeur d'historique partagée

Toutes les pages chargent le même historique, via la constante
`ui_helpers.ACTIVITY_HISTORY_LIMIT` (laisser `cached_load_activities(athlete_id)`
sans second argument). Deux raisons :

- les métriques de charge dépendent de l'historique lu — CTL/ATL/TSB, mais aussi
  l'allure seuil de référence (`reference_threshold_sec`, percentile des sorties
  longues) dont elles découlent : des limites différentes affichaient des TSB
  différents d'une page à l'autre ;
- le cache disque est indexé par `activities_{limit}` : une valeur unique = un seul
  fetch partagé par toutes les pages.

Le seuil de référence est arrondi sur la grille du curseur « Allure seuil » de la
page Forme (pas de 5 s), pour que curseur au repos et calculs internes tombent sur
le même CTL.

Ce que les pages bornent, ce n'est plus le chargement mais l'affichage : filtres de
date (Activités, Heatmap), sélecteur de période (Statistiques), nombre de sorties
incluses dans le prompt (IA Coach), curseur « Activités max » de la Heatmap — ce
dernier compte vraiment, car chaque activité retenue coûte un fetch de streams.

### Séparation des responsabilités

| Couche | Fichier(s) | Rôle |
|---|---|---|
| Données | `garmin_client.py` | Fetch API, cache disque, auth (tokens), transformations |
| Logique métier | `next_session_logic.py`, `heatmap_logic.py`, `comparatif_logic.py`, `coach_logic.py`, `formatting.py` | Calculs purs, testables sans Streamlit |
| UI helpers | `ui_helpers.py` | `require_login()`, `get_garmin_client()`, rendu carte |
| UI | `main.py` + `pages/` + `stats_tabs/` | Affichage uniquement |

### Mapping API Garmin → contrat DataFrame

Le `GarminClient` expose le **même contrat de colonnes** que l'ancien `StravaClient`
(`distance_km`, `avgPace_sec`, `avgHR`…), ce qui a permis de porter les pages et la
logique métier sans les réécrire. Points notables du mapping :

- **Streams** : reconstruits depuis `get_activity_details` (metricDescriptors +
  activityDetailMetrics) ; `grade_smooth` (absent chez Garmin) est recalculé.
- **Splits par km** : recalculés depuis les streams (`compute_km_splits`), car les
  laps Garmin dépendent du réglage autolap de la montre.
- **Cadence** : `averageRunningCadenceInStepsPerMinute` est déjà en pas/min
  (pas de doublement, contrairement aux RPM Strava).
- **`workoutType`** : mappé sur l'`eventType` Garmin (race / training / …).

### Plan Garmin Run Coach

La page Prochaine sortie lit le plan adaptatif du compte plutôt que d'inventer une
séance : `get_training_plans()` donne les plans (celui en cours porte le statut
`Scheduled`), puis `get_adaptive_training_plan_by_id(plan_id)` renvoie ses phases
(`BASE` → `BUILD` → `PEAK` → `TAPER` → `TARGET_EVENT_DAY`) et sa `taskList`, soit
environ une semaine de séances à venir.

L'accueil et la page Prochaine sortie lisent ce plan par le même helper
(`ui_helpers.cached_coach_context`) : les deux annoncent forcément la même séance.

Chaque tâche porte un nom (« Anaérobique »), une description chiffrée
(« 5x1:00@4:15/km », « 147bpm »), une durée estimée, un `trainingEffectLabel` et un
drapeau `restDay`. `coach_logic.py` en extrait les cibles, mappe l'effet visé sur
les types de séance du dashboard et fusionne le tout dans le contrat de
`recommend_session` — la page et le générateur ORS restent donc inchangés.

Deux partis pris :

- Garmin prescrit une **durée**, le générateur de parcours a besoin d'une
  **distance** : elle est estimée depuis l'allure moyenne récente et affichée comme
  telle (« Distance du parcours »), jamais comme une consigne Garmin.
- Quand le coach programme une séance intense alors que la récupération est
  dégradée, la page **avertit sans réécrire la séance** : le plan adaptatif se
  réajuste de lui-même si elle est sautée, et la retoucher ici ferait diverger
  l'application de la montre.

### Séries quotidiennes par plage de dates

Le comparatif annuel a besoin de plusieurs années de sommeil, HRV, VO2max et FC de
repos : un appel par jour serait intenable. `GarminClient` utilise donc les
endpoints « par plage », chacun avec sa fenêtre maximale (au-delà, Garmin répond
400) — le découpage est fait par `date_windows`, et le cache disque est posé
fenêtre par fenêtre :

| Donnée | Endpoint | Fenêtre max | Appels / an |
|---|---|---|---|
| Sommeil | `/wellness-service/wellness/dailySleepsByDate` | 28 j | 13 |
| FC de repos | `/usersummary-service/stats/heartRate/daily/{d}/{f}` | 28 j | 13 |
| HRV | `/hrv-service/hrv/daily/{d}/{f}` | 365 j | 1 |
| VO2max | `/metrics-service/metrics/maxmet/daily/{d}/{f}` | 365 j | 1 |
| Prédictions | `get_race_predictions(_type="daily")` | 365 j | 1 |

Soit une trentaine d'appels par année comparée, une seule fois par TTL de cache.
Une fenêtre en échec est loggée et ignorée : la page s'affiche avec les années
disponibles au lieu de tomber en erreur.

---

## 🤖 Discuter avec Claude (licence Pro / Max)

Un abonnement claude.ai ne se branche pas dans une application tierce : on
l'utilise via **Claude Code** ou **Claude Desktop**, qui lancent le serveur MCP du
projet sur ta machine. Claude lit alors tes métriques **calculées** par le
dashboard — fraîcheur, séance du jour, dérive cardiaque, plan vers ton objectif —
et tu en discutes (« pourquoi je stagne ? », « mon plan est-il trop chargé ? »).

Outils exposés : `daily_briefing`, `training_load`, `activity_analysis`,
`aerobic_trend`, `health_watch` (veille santé), `running_form` (foulée à allure
égale, pic de sortie), `race_plan_preview`, `current_goal`, plus l'accès en lecture aux
~100 méthodes de lecture de `garminconnect` (`garmin_call`). **Lecture seule** (liste
blanche `get_*` / `count_*` / `download_*`) : envoyer des séances à la montre se
fait uniquement depuis la page Objectif, sur confirmation.

**Commandes prêtes à l'emploi** (prompts MCP, menu `/` de Claude Code ou le
bouton « + » de Claude Desktop) : `bilan_semaine` (niveau débutant ou confirmé),
`pourquoi_fatigue`, `seance_du_jour`, `prepa_course` (distance, date, temps visé),
`debrief` (dernière sortie ou un identifiant), `ajuste_plan` (« vacances du 3 au
10 », « genou sensible »…). Elles s'adaptent à ta situation au moment où tu les
lances : si Run Coach pilote la montre, Claude commente ses séances sans en
proposer d'autres ; si la veille santé s'allume, la récupération passe en premier.

1. Amorcer la session du serveur (une fois) : le script demande email, mot de
   passe (s'ils ne sont pas dans `.env`) et code MFA, puis enregistre les tokens
   dans `~/.garminconnect-mcp` :
   `uv run --no-project --with-requirements requirements.txt python test_connection.py`
2. **Claude Code** : ouvre le dossier du projet, le fichier `.mcp.json` déclare
   le serveur (`uv` requis). Vérifie avec `/mcp`.
3. **Claude Desktop** : ajoute dans `claude_desktop_config.json` :
   ```json
   { "mcpServers": { "garmin": {
       "command": "uv",
       "args": ["run", "--no-project", "--with-requirements",
                "/chemin/vers/garmin-running-dashboard/requirements.txt",
                "python", "/chemin/vers/garmin-running-dashboard/garmin_mcp/server.py"]
   } } }
   ```

Les analyses du MCP utilisent les seuils par défaut : les réglages du mode Pro
(propres à ta session du dashboard) ne s'y appliquent pas.

> **Mise à jour depuis une version précédente** : le serveur MCP utilisait
> `~/.garminconnect`. Relance une fois `test_connection.py` (ou renomme le dossier
> en `~/.garminconnect-mcp`) pour lui recréer sa session.

Le serveur lit l'objectif enregistré par le dashboard de dev (`app/.data`) mais
garde ses propres tokens (`~/.garminconnect-mcp`) : deux processus qui partagent un
jeton de rafraîchissement se l'invalideraient mutuellement.

---

## Dépannage

**L'application ne démarre pas**
```bash
docker compose logs app
```

**Erreur de connexion Garmin / 401**
- Bouton « Déconnexion » puis reconnexion (régénère les tokens)
- Vérifier `GARMIN_EMAIL` / `GARMIN_PASSWORD`
- MFA : le code est envoyé par email par Garmin, il expire vite

**Erreur 429 (Too Many Requests)**
- Garmin rate-limite : attendre quelques minutes (voire heures) ; le cache disque
  limite fortement les appels réels

**Readiness / Body Battery vides**
- Ces données nécessitent une montre compatible (Body Battery : la plupart ;
  Training Readiness : Fenix 7+, Epix 2, FR 265/965… — pas le FR 255)
- Quand la montre ne calcule pas la métrique, Garmin répond par une liste vide.
  Le serveur MCP le rend explicite : `get_training_readiness` retourne
  `{"supported": false, …}` et le passe-plat `garmin_call` `{"empty": true, …}`,
  avec les substituts à utiliser (statut VFC, sommeil, Body Battery, statut
  d'entraînement). Ce n'est pas une erreur d'appel.

---

## 🔐 Données et services tiers

**Ce qui reste chez toi** (données de santé : FC, HRV, sommeil, GPS, dont la zone
de ton domicile) :

| Donnée | Docker | Hors Docker |
|---|---|---|
| Cache des réponses Garmin et des streams | volume `app_cache` (`/app/.cache`) | `~/.cache/garmin-dashboard` (ou `CACHE_DIR`) |
| Objectif, plan validé, journal des envois | volume `app_data` (`/app/.data`) | `~/.local/share/garmin-dashboard` (ou `DATA_DIR`) |
| Jetons Garmin du dashboard | volume `garmin_tokens` (`/app/.garmin`) | `~/.garminconnect` (ou `GARMIN_TOKENSTORE`) |
| Jetons Garmin du serveur MCP | — | `~/.garminconnect-mcp` (ou `GARMIN_TOKENSTORE_MCP`) |

**Ce qui part vers des tiers :**

- **Garmin Connect** : toutes les lectures, et l'envoi de séances au calendrier
  (page Objectif, sur confirmation).
- **Tuiles de carte** : OpenStreetMap (cartes des sorties, parcours) et Esri (page
  Carte). Ces serveurs voient ton IP et les zones affichées, donc la région de tes
  sorties.
- **CDN de Folium** (page Carte) : Leaflet, jQuery, Bootstrap… chargés depuis
  jsdelivr / cdnjs.
- **OpenRouteService** (page Prochaine sortie, si une clé est configurée) : le point
  de départ du parcours, souvent ton domicile. L'appel est mis en cache une heure.
- **Claude (Anthropic)**, si tu branches le serveur MCP : les métriques que Claude
  demande pendant la conversation.
- **Le LLM de ton choix**, si tu colles un prompt de la page Coach IA : le contexte
  affiché dans le prompt.

**Tout effacer :**

```bash
docker compose down -v                                   # dev : cache, plan, jetons
docker compose -f docker-compose.prod.yml down -v        # prod (+ certificats Caddy)
rm -rf ~/.garminconnect-mcp                              # jetons du serveur MCP
rm -rf ~/.cache/garmin-dashboard ~/.local/share/garmin-dashboard ~/.garminconnect  # hors Docker
```

Si tu héberges l'outil **pour quelqu'un d'autre**, ce sont des données de santé
(article 9 du RGPD) : tu en deviens responsable de traitement.

---

## Licence

**Aucune licence n'est attachée à ce dépôt.** Le code reste donc, par défaut,
« tous droits réservés » : il est public pour être lu, cloné et installé par les
personnes à qui je l'ai partagé, mais aucun droit de réutilisation, de
modification ou de redistribution n'est accordé. Écris-moi si tu veux en faire
autre chose.

Le projet s'appuie sur des bibliothèques open-source :
[python-garminconnect](https://github.com/cyberjunky/python-garminconnect),
[Streamlit](https://streamlit.io),
[OpenRouteService](https://openrouteservice.org),
[Plotly](https://plotly.com),
[Caddy](https://caddyserver.com).
Non affilié à Garmin.
