# 🏃 Garmin Running Dashboard

Tableau de bord pour l'analyse de tes données de course à pied.
Se connecte à **Garmin Connect** (lib non officielle `garminconnect`), génère des
**parcours inédits** via OpenRouteService, et produit des **prompts d'analyse IA**
prêts à coller dans Claude, ChatGPT ou Gemini.

Portage Garmin du projet `run` (ex-Strava) : même architecture (Streamlit multipage,
logique pure testée, cache à deux niveaux, Docker + Caddy), mais **mono-utilisateur** —
l'API Garmin non officielle s'authentifie par identifiants, pas par OAuth multi-comptes.

---

## Fonctionnalités

- **Accueil** — cockpit du jour : sommeil, HRV, Body Battery, FC repos, verdict de
  forme, métriques semaine/mois, **séance du coach Garmin** (la même que sur
  Prochaine sortie), dernière sortie en résumé, kilométrage des chaussures
  (gear Garmin)
- **Activités** — liste filtrée par date, type et distance ; détail complet avec
  laps, streams (altitude / allure / FC) et carte GPS
- **Statistiques** — 5 onglets : volume, allure, FC (zones réelles du profil Garmin),
  cadence, régularité
- **Forme & Récup** — charge d'entraînement (CTL/ATL/TSB) calculée sur **toutes
  les activités** (course à l'allure, wing / vélo / natation / renfo via la charge
  Garmin), croisée avec la récupération (HRV vs baseline, sommeil, Body Battery,
  FC repos, stress) et **verdict du jour** (prêt à performer / normal / lève le
  pied)
- **Progression** — records personnels Garmin, prédictions de course natives
  (+ Riegel) avec historique d'évolution, courbe de VO2max
- **Prochaine sortie** — la séance du **plan Garmin Run Coach** (nom, cibles,
  durée, phase du plan, programme de la semaine avec renfo et repos imposés), un
  avertissement quand le coach programme une séance intense sur une récupération
  dégradée, + parcours en boucle généré sur OpenStreetMap et export GPX. Sans plan
  actif, repli sur la recommandation calculée depuis la charge (TSB) et **modulée
  par la récupération** (HRV/sommeil dégradés → séance rétrogradée)
- **Heatmap** — cartes de chaleur des courses (fréquence, allure, FC, pente,
  dénivelé signé) sur fond CartoDB sombre
- **IA Coach** — deux prompts prêts à coller dans n'importe quel LLM :
  **analyse d'entraînement** (sorties, charge, HRV, sommeil, records, prédictions)
  et **idées de repas** (séances des prochains jours issues du plan Garmin,
  dépense énergétique sur 7 jours, contraintes alimentaires saisies)
- **Comparatif annuel** — l'année en cours superposée aux précédentes sur un axe
  « jour de l'année » : charge (CTL/ATL/TSB), volume cumulé (km, D+, heures),
  physiologie (VO2max, FC repos, prédictions) et récupération (sommeil, HRV),
  avec un bandeau « à la même date » qui chiffre l'écart avec l'an dernier et un
  face-à-face de **la sortie du jour** avec celle de la même date les autres années

### Différences vs la version Strava

| | Strava (`run`) | Garmin (`gar`) |
|---|---|---|
| Authentification | OAuth multi-user, token en session | Identifiants + tokens garth persistés (~1 an), mono-user |
| Segments / KOM | ✅ page Segments | ❌ pas d'équivalent API Garmin |
| Prédictions de course | Formule de Riegel | **Natives Garmin** + Riegel |
| Zones FC | Configurées dans Strava | Réelles du profil Garmin (par sport) |
| Santé (sommeil, Body Battery, readiness) | ❌ | ✅ page dédiée |
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
  contractuellement que l'usage soit toléré. Tu utilises tes propres identifiants,
  à tes risques.
- **Ton mot de passe Garmin est stocké en clair** dans le fichier `.env` (il n'est
  utilisé qu'au premier login : ensuite ce sont les tokens garth). Garde ce fichier
  hors de tout dépôt — il est dans `.gitignore`.
- **L'application n'a aucune authentification propre.** Streamlit écoute sur
  `0.0.0.0:8501`, donc toute machine de ton réseau local peut ouvrir le dashboard
  et voir tes données. Pour une exposition sur Internet, passe par
  `docker-compose.prod.yml` (Caddy + HTTPS) et ajoute une protection d'accès.
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

Streamlit démarre sur le port `8501` avec hot-reload.

### 3. Se connecter à Garmin

Ouvre **[http://localhost:8501](http://localhost:8501)**. Si `GARMIN_EMAIL` /
`GARMIN_PASSWORD` sont dans `.env`, le formulaire est pré-rempli — clique sur
**« Se connecter à Garmin »**. Si ton compte a le MFA activé, un champ apparaît
pour saisir le code reçu par email.

Les tokens garth sont ensuite persistés dans `app/.garmin/` (dev) ou le volume
`garmin_tokens` (prod) : les démarrages suivants se connectent **automatiquement**,
sans mot de passe ni MFA, pendant environ un an.

---

## Déploiement en production

Identique au projet `run` : Caddy en frontal HTTPS (Let's Encrypt auto).

```bash
# .env : compléter PUBLIC_DOMAIN, ACME_EMAIL, STREAMLIT_BROWSER_SERVER_ADDRESS
docker compose -f docker-compose.prod.yml up -d --build
```

⚠️ L'app étant **mono-utilisateur** et l'authentification simple, ne pas exposer
publiquement sans protection supplémentaire (auth basique Caddy, VPN, Tailscale…) :
quiconque accède à l'URL voit tes données une fois la session serveur connectée.

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
.venv/bin/pip install -r app/requirements.txt -r requirements.txt pytest

# Puis :
.venv/bin/python -m pytest tests/ -v

# ── Test de connexion CLI ────────────────────────────────────────────
.venv/bin/python test_connection.py

# ── Prod ─────────────────────────────────────────────────────────────
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f app
```

Pour se déconnecter : bouton **« Déconnexion »** dans la barre latérale
(supprime le tokenstore).

---

## Variables d'environnement

| Variable | Défaut | Description |
|---|---|---|
| `GARMIN_EMAIL` | — | Email du compte Garmin (pré-remplit le formulaire) |
| `GARMIN_PASSWORD` | — | Mot de passe (jamais stocké, sert au premier login) |
| `GARMIN_TOKENSTORE` | `/app/.garmin` (Docker) ou `~/.garminconnect` | Dossier des tokens garth |
| `CACHE_DIR` | `/app/.cache` (Docker) ou `~/.cache/garmin-dashboard` | Cache disque des appels API |
| `CACHE_TTL` | `3600` | Durée du cache disque en secondes |
| `ORS_API_KEY` | — | Clé OpenRouteService (page Prochaine sortie) |
| `PUBLIC_DOMAIN` | — | *(prod)* Domaine servi par Caddy |
| `ACME_EMAIL` | — | *(prod)* Email Let's Encrypt |
| `STREAMLIT_BROWSER_SERVER_ADDRESS` | `localhost` | *(prod)* Hostname annoncé au navigateur |

---

## Structure du projet

```
gar/
├── docker-compose.yml          # Stack dev (Streamlit avec hot-reload)
├── docker-compose.prod.yml     # Stack prod (Caddy + Streamlit, volumes nommés)
├── Caddyfile                   # Reverse-proxy HTTPS + headers de sécurité
├── .env.example                # Template de configuration
├── pytest.ini                  # Configuration des tests (pythonpath = app)
├── test_connection.py          # Test CLI de connexion Garmin
├── garmin_mcp/                 # Serveur MCP Garmin (indépendant du dashboard)
├── tests/
│   ├── conftest.py               # Fixtures pytest et stubs Streamlit/Plotly
│   ├── test_garmin_client.py     # Transformations Garmin, cache, erreurs
│   ├── test_forme_logic.py       # Verdict de forme, rétrogradation de séance
│   ├── test_progression_logic.py # Records, Riegel, historique prédictions
│   ├── test_comparatif_logic.py  # Alignement des années, cumuls, instantanés
│   ├── test_coach_logic.py       # Plan Garmin Run Coach, cibles, fusion reco
│   ├── test_formatting.py        # decimate, map_zoom
│   ├── test_next_session.py      # Logique TSB / recommandation / GPX
│   └── test_heatmap_logic.py     # Haversine, detect_home, rasterize, normalize
└── app/
    ├── Dockerfile              # Image Docker (Python 3.12-slim, user non-root)
    ├── requirements.txt        # Dépendances Python
    ├── main.py                 # Accueil (cockpit du jour) + login Garmin (MFA)
    ├── garmin_client.py        # Client Garmin + cache + transformations
    ├── chart_theme.py          # Palette validée + template Plotly gar_dark
    ├── next_session_logic.py   # Logique pure : TSB, recommandation, GPX
    ├── forme_logic.py          # Logique pure : verdict forme, rétrogradation
    ├── progression_logic.py    # Logique pure : records, Riegel, prédictions
    ├── comparatif_logic.py     # Logique pure : alignement des années, cumuls
    ├── coach_logic.py          # Logique pure : plan Garmin Run Coach, cibles
    ├── heatmap_logic.py        # Logique pure : rasterize, blur, normalize
    ├── formatting.py           # Logique pure : pace, types d'activité
    ├── ui_helpers.py           # require_login(), get_garmin_client(), carte
    ├── stats_tabs/             # Onglets de Statistiques (+ charge sur Forme)
    └── pages/
        ├── 1_Activities.py     # Liste et détails des activités
        ├── 2_Stats.py          # Volume, allure, FC, cadence, régularité
        ├── 3_Forme.py          # Charge × récupération + verdict du jour
        ├── 4_Progression.py    # Records, prédictions, VO2max
        ├── 5_Next_Session.py   # Séance du coach Garmin + parcours ORS
        ├── 6_Heatmap.py        # Heatmaps multi-calques (Folium)
        ├── 7_AI_Coach.py       # Prompts LLM avec contexte complet
        └── 8_Comparatif.py     # Années superposées (charge, volume, physio, récup)
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
| Données | `garmin_client.py` | Fetch API, cache disque, auth garth, transformations |
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

## Licence

**Aucune licence n'est attachée à ce dépôt.** Le code reste donc, par défaut,
« tous droits réservés » : il est public pour être lu, cloné et installé par les
personnes à qui je l'ai partagé, mais aucun droit de réutilisation, de
modification ou de redistribution n'est accordé. Écris-moi si tu veux en faire
autre chose.

Le projet s'appuie sur des bibliothèques open-source :
[python-garminconnect](https://github.com/cyberjunky/python-garminconnect),
[garth](https://github.com/matin/garth),
[Streamlit](https://streamlit.io),
[OpenRouteService](https://openrouteservice.org),
[Plotly](https://plotly.com),
[Caddy](https://caddyserver.com).
Non affilié à Garmin.
