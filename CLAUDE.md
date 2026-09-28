# CLAUDE.md — Garmin Running Dashboard

## Stack

- **Streamlit 1.61+** — `app/main.py` est un **routeur** `st.navigation`
  (`app/nav.py`, pages de `app/pages/`) ; 1.61 pour `server.allowedHosts`
  (anti DNS rebinding), 1.52 pour `st.metric(delta_arrow=)`, `theme.fontFaces`,
  `st.container(horizontal=)`
- **Python 3.12**, Pandas, Plotly, NumPy
- **Docker Compose** — service `app` (Streamlit), service `caddy` (HTTPS) en prod
- **Garmin Connect** via la lib non officielle `garminconnect` (0.3.6, client
  interne, plus de garth) — **mono-utilisateur**, tokens persistés dans le
  tokenstore (`garmin_tokens.json`, ~1 an de validité)
- **OpenRouteService API** — génération de parcours GPX (page 5)

## Origine

Portage d'un projet Strava équivalent vers l'API Garmin.
Le `GarminClient` expose le **même contrat de DataFrames** que l'ancien
`StravaClient` — ne pas casser ce contrat, c'est lui qui permet aux pages et à la
logique pure de rester communes. Abandonné au passage : page Segments (pas
d'équivalent Garmin), multi-user OAuth.

Pages (réorganisation juillet 2026, navigation en pôles septembre 2026) :
`0_Accueil` (cockpit du jour : carte séance, fraîcheur, semaine, signaux via
`home_logic.py`), `1_Activities` (explorateur cliquable + répartition 80/20 via `activities_logic.py`,
seul endroit avec le détail complet d'une activité),
`2_Stats` (5 onglets — la charge a déménagé), `3_Forme` (fusion ex-Santé +
ex-onglet Charge + verdict croisé TSB×HRV×sommeil via `forme_logic.py`, ACWR et
monotonie en mode Pro), `4_Progression` (records, prédictions + historique,
VO2max, efficacité aérobie et dérive via `physio_logic.py`), `5_Next_Session`
(reco **modulée** par la récupération :
`recommend_session(df, downgrade=n)` en **repli** — la source primaire est le
plan Garmin Run Coach via `coach_logic.py`), `6_Heatmap`, `7_AI_Coach` (contexte
enrichi forme/HRV/sommeil/records), `8_Comparatif` (années superposées sur un axe
jour-de-l'année via `comparatif_logic.py`), `11_Calendrier` (grille du mois
cliquable, comparaison de deux sorties via `compare_logic.py`),
`10_Jour_de_course` (GPX → allure au km, stratégie progressive, chaleur,
ravitaillement), `9_Objectif` (course datée → plan
course + renfo via `race_plan_logic.py`, envoi au calendrier Garmin). Le thème graphique central est
`chart_theme.py` (palette validée par le validateur dataviz — ne pas réordonner
les slots catégoriels ni réutiliser les couleurs status comme séries).

## Navigation et thème (ne pas casser)

- **Routeur** : `main.py` = `set_page_config` + `inject_theme()` + login (seule
  page enregistrée sans session) puis `st.navigation(position="hidden")` et
  `nav.render_header`. Les pôles et leurs pages vivent dans `nav.POLES` (4 pôles :
  Aujourd'hui / Entraînement / Progrès / Objectif) ; ajouter une page = l'y
  déclarer. Tous les liens sont des `st.page_link` (navigation côté client : la
  session, le mode Light/Pro et les réglages survivent) — jamais de `<a href>`
  vers une page, qui rechargerait la session.
- **En-tête** unique (bascule Light/Pro, Actualiser, Compte, Prompt coach IA) :
  les pages n'en rendent pas ; la barre latérale ne porte que les filtres de la
  page et les réglages Pro. La barre d'onglets du bas n'apparaît que < 640 px (CSS).
- **Thème « Piste claire »** : tokens dans `chart_theme.py` (papier `SURFACE`,
  cartes `SURFACE_2`, encre `INK`, `ACCENT` volt), repris par `config.toml` et
  `ui_theme.py`. L'accent volt ne sert qu'en **aplat** (jamais du texte sur clair) ;
  états = pastilles `STATUS_TEXT`/`STATUS_BG` (≥ 4,5:1). Cartes =
  `st.container(key="card-…")`. Tout texte externe injecté en HTML passe par
  `ui_theme.esc` (une ligne vide rouvrirait le Markdown). Contrastes gardés par
  `tests_ui/test_theme_ui.py`.
- **Tests UI** : `logged_in(name)` passe par le routeur (premier run puis
  `switch_page`) — `AppTest.from_file` sur une page seule ne connaîtrait pas les
  `st.page_link`.

## Lancer le projet

```bash
docker compose up            # dev local — Streamlit sur 127.0.0.1:8501
.venv/bin/python -m pytest tests/ -q      # logique pure (~880), hors Docker
.venv/bin/python -m pytest tests_ui/ -q   # pages en headless (AppTest + FakeGarmin)
.venv/bin/python -m pytest tests_e2e/ -q  # vrai navigateur (Playwright), démo FakeGarmin
.venv/bin/python test_connection.py       # amorce le tokenstore du serveur MCP
```

Le venv `.venv/` n'est pas versionné : le recréer avec
`uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r app/requirements.txt -r requirements.txt pytest pytest-cov playwright`
(+ `.venv/bin/playwright install chromium`, sinon `tests_e2e/` se rabat sur le
Chrome du système). Les suites se lancent **séparément** (`tests/conftest.py` remplace
streamlit par un mock). `garmin_mcp/` est le serveur MCP : il réutilise la
logique de `app/` (voir « Serveur MCP »).

**Hôtes acceptés** : `server.allowedHosts` (config.toml : localhost/127.0.0.1 ;
prod : `STREAMLIT_SERVER_ALLOWED_HOSTS=${PUBLIC_DOMAIN}`) refuse le WebSocket d'un
Host inconnu — c'est ce qui empêche un site tiers de piloter le dashboard local
par DNS rebinding (écriture Garmin activée en dev). `server.address = "localhost"`
dans config.toml parce que le défaut de Streamlit écoute sur toutes les interfaces.

**Publication du port** : `docker-compose.yml` publie sur `127.0.0.1:8501:8501`,
jamais `8501:8501`. L'app n'a aucune authentification : un bind `0.0.0.0` expose
les données Garmin à tout le réseau local, et Docker contourne UFW (le pare-feu
ne rattraperait pas le coup). `--server.address=0.0.0.0` dans le Dockerfile est
l'écoute *interne* au conteneur et doit rester telle quelle.

## Règles Streamlit

- **Ne jamais utiliser `use_container_width=`** — remplacé par `width='stretch'`
  ou `width='content'` depuis Streamlit 1.44+.
- **Cartes Plotly** : `go.Scattermap` et `layout.map` — `go.Scattermapbox` /
  `layout.mapbox` sont dépréciés.
- **Widgets avec `key=`** : ne pas passer `value=` en même temps. Initialiser via
  `st.session_state` avant la déclaration du widget.
- **Tabs sensibles aux reruns** : `st.radio(horizontal=True, key=...)` plutôt que
  `st.tabs()` (cf. `2_Stats.py`).
- **Sliders** : `value` et `max_value` alignés sur `step`.

## Spécificités API Garmin (ne pas casser)

- **Auth** : `garminconnect` 0.3.6 — le client HTTP est `api.client` (PAS
  `api.garth`, garth n'est plus une dépendance). En mode `return_on_mfa=True`, `login()` retourne AVANT de charger
  le profil et NE dumpe PAS les tokens → `login_with_credentials` /
  `complete_mfa` dans `garmin_client.py` gèrent le dump + rechargent une session
  propre via `resume_session()`. Ne pas "simplifier" ce flux.
- **Cadence** : Garmin envoie déjà des pas/min (`averageRunningCadenceInStepsPerMinute`,
  `averageRunCadence` des laps, stream `directDoubleCadence`) — **ne jamais doubler**
  (contrairement aux RPM Strava).
- **Streams** : reconstruits par `build_streams()` depuis `get_activity_details`
  (metricDescriptors → index de colonnes). `grade_smooth` est recalculé
  (`compute_grade_stream`), `latlng[i]` vaut `None` quand le GPS manque (les
  consommateurs testent `if not ll`).
- **Splits par km** : `compute_km_splits()` depuis les streams — les laps Garmin
  (`get_activity_splits`) dépendent du réglage autolap de la montre.
- **`workoutType`** : chaîne eventType Garmin (`race`, `training`, `uncategorized`…),
  traduite par `formatting.event_type_label`. Ce n'est plus un entier Strava.
- **Zones FC** : endpoint interne `/biometric-service/heartRateZones` via
  `api.connectapi` (voir `get_hr_zones_definition`) — format retourné :
  `[{"min", "max"}, …]`, `max=-1` pour la dernière zone.
- **Rate limit** : cooldown `API_COOLDOWN_S` (0.4 s) après chaque appel réel ;
  les hits de cache sont instantanés. Garmin peut renvoyer 429 → message dédié
  dans `safe_load_activities`.
- **Training Readiness** : renvoie `[]` si la montre ne le supporte pas — ne pas
  traiter ça comme une erreur (le serveur MCP renvoie `{"supported": false}`).
- **Erreurs HTTP** : lire le code via `garmin_client._http_status` (attribut ou
  « API Error NNN »), jamais en cherchant « 404 » dans le texte.
- **Plan adaptatif** : le plan actif est celui dont `trainingStatus.statusKey`
  vaut `Scheduled` (Garmin garde l'historique des plans `Completed`).
  `get_adaptive_training_plan_by_id` renvoie `taskList` (~1 semaine à venir, avec
  `restDay` et `adaptiveCoachingWorkoutStatus`) et les phases
  (`BASE`/`BUILD`/`PEAK`/`TAPER`/`TARGET_EVENT_DAY`). Les cibles vivent dans
  `workoutDescription` en texte (`5x1:00@4:15/km`, `147bpm`) — parsées par
  `parse_workout_target()`.
- **Séries par plage de dates** (page Comparatif) : endpoints `connectapi` avec une
  fenêtre maximale par endpoint — 28 jours pour le sommeil
  (`dailySleepsByDate`) et la FC de repos (`usersummary-service/stats/heartRate`),
  365 pour la HRV (`hrv-service/hrv/daily`), le VO2max
  (`metrics-service/metrics/maxmet/daily`) et les prédictions. Au-delà, Garmin
  répond 400 : passer par `date_windows()` plutôt que d'élargir la plage.

## Profondeur d'historique (ne pas casser)

- Toutes les pages chargent `cached_load_activities(athlete_id)` **sans second
  argument** → `ui_helpers.ACTIVITY_HISTORY_LIMIT`. Une limite propre à une page
  ferait réapparaître deux CTL/TSB différents dans l'app et dédoublerait le cache
  (`activities_{limit}`).
- `reference_threshold_sec()` est arrondi sur la grille du curseur d'allure seuil
  (`THRESHOLD_SLIDER_MIN/MAX/STEP`) : c'est ce qui aligne le CTL de `tab_charge`
  (curseur au repos) sur celui de `compute_tsb`.
- Pour restreindre une vue, filtrer l'affichage — pas le chargement.

## Cache (ne pas casser)

- Deux niveaux : `@st.cache_data` (RAM, nonce per-session) puis cache disque JSON
  `CACHE_DIR/{athlete_id}/{md5}.json` (TTL `CACHE_TTL`).
- `CACHE_DIR` : `/app/.cache` dans Docker, `~/.cache/garmin-dashboard` hors Docker
  (surchargable par env). Le `mkdir` de `_cache_set` est DANS le try/except :
  une erreur d'écriture cache ne doit jamais faire échouer l'appel API.
- `refresh_data` (bouton Actualiser de l'en-tête) : invalide cache disque + bump
  `_cache_nonce` — ne pas utiliser `st.cache_data.clear()`.

## Logique métier (ne pas casser)

- **TSB / CTL / ATL** dans `next_session_logic.py` — fonctions pures, testées.
  Le PMC agrège **toutes** les activités (`daily_tss`) : la course par son allure,
  le reste (wing, vélo, natation, renfo) par sa charge Garmin `trainingLoad`
  convertie via `cross_training_factor()` — un facteur recalibré sur les courses,
  qui portent les deux métriques. Les pages passent donc le DataFrame **complet**
  aux fonctions de charge, jamais un filtre `activityType == "running"` ;
  `recommend_session(running_df, load_df=df)` sépare les deux rôles (cibles de
  séance sur les courses, TSB sur tout). Sommer la charge Garmin brute au TSS
  d'allure ferait changer le CTL d'unité selon la part de sport croisé.
  L'allure seuil vient de `reference_threshold_sec()` : un seuil **unique** pour
  tout l'historique, sinon les TSS ne sont plus comparables d'une année à l'autre —
  et il ne lit que la course, même sur le DataFrame complet.
  Le **TSB a une seule définition** : `tsb = ctl - atl` en fin de journée, posée
  dans `compute_pmc_series()` et simplement relue par `compute_tsb()`. C'est ce
  qui aligne la métrique du haut de `3_Forme`, celle de `tab_charge`, l'Accueil,
  `5_Next_Session`, `7_AI_Coach` et la courbe du Comparatif — et qui fait que le
  TSB tracé est bien l'écart vertical entre les courbes CTL et ATL. Une variante
  « fraîcheur d'avant-séance » ferait réapparaître deux TSB sur la même page.
- **Heatmap** : logique pure dans `heatmap_logic.py`, testée.
- **Coach Garmin** : `coach_logic.py`, testé. Accueil et `5_Next_Session` passent
  tous deux par `ui_helpers.cached_coach_context()` + `merge_coach_into_recommendation()`
  — toute page qui annonce une séance doit suivre ce chemin, sinon elle affiche une
  reco divergente de celle de la montre. `merge_coach_into_recommendation()`
  fait piloter la séance par le plan adaptatif tout en respectant le contrat de
  `recommend_session` — ne pas casser ce contrat, la page et le générateur ORS en
  dépendent. On **avertit** sans réécrire la séance quand la récup est dégradée :
  réécrire ferait diverger le dashboard de la montre.
- **Comparatif annuel** : `comparatif_logic.py`, testé. `aligned_doy()` corrige les
  années bissextiles (le 1er mars vaut 60 partout) — c'est ce qui garantit que les
  courbes des années se superposent sur le bon jour.
- **Tests** : `tests/` couvre la calibration (`cross_training_factor`), la
  décomposition (`daily_tss`) et la non-régression du PMC course-seule — un
  historique 100 % course doit donner exactement les mêmes CTL/ATL/TSB qu'avant.
- **Contrat DataFrame activités** : colonnes `activityId, startTimeLocal,
  activityName, activityType, distance_km, duration_min, avgPace, avgPace_sec,
  avgHR, maxHR, avgCadence, calories, elevationGain, avgSpeed_ms, startLat,
  startLon, workoutType, trainingLoad, vo2max`.

## Analyses, plan, écriture Garmin (ne pas casser)

- **Physio** (`physio_logic.py`) : FC calée sur la cadence = plateau ≥ 120 s ET
  marche brutale ≥ 12 bpm (sinon un finish accéléré ou une répétition au seuil
  serait signalé). Dérive Pa:HR invalide si < 40 min, CV vitesse > 0,15, sortie
  progressive (+5 %), effort relâché (vitesse ET FC en baisse), dénivelé inégal ;
  ralentir à FC constante EST la dérive. Altitude lissée avant le D+. Calibré
  sur 16 sorties réelles (non versionnées : données de santé), cas limites
  rejoués en synthétique dans `tests/test_physio_logic.py` ; ne pas assouplir
  sans refaire la mesure sur de vrais streams (cache local, lecture seule).
- **Streams** : bucket `streams/` du cache, TTL `STREAMS_CACHE_TTL` (30 j),
  conservé par « Actualiser » ; les boucles multi-activités sont bornées
  (`DECOUPLING_TREND_MAX_RUNS`) et s'arrêtent au premier refus Garmin.
- **Plan** (`race_plan_logic.py`) : déterministe ; semaines calendaires ; allures
  de prescription = course récente > prédiction Garmin × 1,03 > entraînements —
  JAMAIS `reference_threshold_sec` (réservé au TSS) ; volume annoncé = volume
  prescrit (±10 %) ; renfo jamais la veille d'une séance clé ni le jour de la
  sortie longue, arrêt J-9 (règles sourcées dans `SOURCES`).
- **Plan figé** : une fois validé, c'est `goal_store.validated.plan` qui
  s'affiche et s'envoie, pas un recalcul du jour.
- **Écriture Garmin** (page Objectif uniquement, `GARMIN_WRITE_ENABLED`) : l'**envoi**
  est bloqué si un plan Run Coach est actif OU si son état est inconnu (lecture
  fraîche, `get_training_plans(strict=True)`). Le **retrait** des séances du
  dashboard (étiquetées) reste permis dans ce cas, volontairement : c'est le
  nettoyage quand on passe à Run Coach ; il ne touche jamais une séance non étiquetée. Chaque séance porte une étiquette
  `[GD-<plan>-<jour>-<run|str>]` et une empreinte de contenu ; journal sous
  verrou (`goal_store.locked`, réentrant), réconciliation par étiquette avec
  planification vérifiée, et `remove_workout(required_tag=)` avant toute
  suppression. Dédup par créneau (jour + course/renfo), pas par type.
- **Modes Light/Pro** (`ui_mode.py`) : état hors clés de widget ; les réglages
  Pro ne portent que sur les seuils physio, jamais sur CTL/ATL (un seul TSB).
- **Intensité** (`activities_logic`) : IF = allure seuil du TSS
  (`reference_threshold_sec`) ÷ allure, TSS = `next_session_logic.pace_tss` —
  exactement la charge du PMC (testé). Zones : < 0,78 récup … > 1,03 VMA.
- **Projections** (`forecast_logic`) : tendance Theil-Sen sur 8 semaines, départ
  ancré sur la médiane des 7 derniers jours (sinon une saison en V projetait une
  régression alors que la forme remonte), gains amortis (τ 75 j), plafonds
  ±2 %/mois (temps) et ±1 pt/mois (VO2max), bande ≥ ±1 % / ±1 pt ; aucune
  projection sous 8 points ou 4 semaines. Toujours affichée comme estimation.
- **Veille santé** (`illness_logic`) : dernière nuit vs norme J−30…J−3 (médiane,
  MAD, planchers de dispersion) ; un signal compte s'il dévie ≥ 2 σ ET d'un écart
  physiologique (FC +4 bpm, respiration +1/min, HRV −10 %, SpO2 −2 pts). Un signal
  ISOLÉ n'alerte que s'il persiste 2 nuits ou dépasse 3 σ (sinon, rejoué sur 60
  nuits réelles, la carte s'allumait une nuit sur sept) — un signal ignoré perd
  `flagged` (statut `ignored`), sinon la carte et le MCP le listaient sous un
  verdict « rien ». Sur l'Accueil, la carte santé ne passe en tête qu'au niveau
  ≥ 1 (verte, elle chassait une vraie alerte de la coupe à 4). Signal absent ou norme
  < 10 nuits : dit, pas inventé. `load_health_frame` = chemin unique Accueil / MCP.
- **Foulée** (`running_form_logic`) : toujours à allure égale (résidu d'un modèle
  linéaire en vitesse, 6 semaines vs 12 précédentes, ≥ 5 sorties par fenêtre).
  Colonnes de dynamique **optionnelles** du DataFrame (`garmin_client.DYNAMICS_COLUMNS`,
  NaN sans capteur) : le contrat des 19 colonnes reste intact. Pic de sortie :
  ratio à la plus longue des 30 jours, > 1,10 à surveiller, > 1,30 élevé (au
  pour-cent près). Une séance PRÉVUE se compare aussi à la plus longue séance
  prévue avant elle (un plan à +10 %/semaine ne se signale pas contre lui-même) ;
  les séances prévues viennent de `planned_runs` = la même source que la semaine
  de l'Accueil (Run Coach s'il pilote, distance estimée à l'allure médiane).
  Reprise après ≥ 30 jours sans courir (avec un historique plus ancien) : une
  sortie ≥ 8 km, faite ou prévue, est signalée (`level = "comeback"`).
- **Jour de course** (`raceday_logic`) : GPX de plus de 5 Mo refusé (et
  `server.maxUploadSize = 5` dans config.toml) ; DOCTYPE/ENTITY refusés par une
  pré-passe **expat** (tout encodage, toute position — un filtre sur les octets
  se contournait en UTF-16 ; expat ≥ 2.4.1 bloque de toute façon l'explosion
  d'entités). La trace (`trk`) prime sur la route (`rte`) — les mettre bout à
  bout triplait la distance ; traces contiguës (≤ 200 m) enchaînées SAUF si la
  chaîne est déjà bouclée (variantes 10 km / semi partant de la même arche) —
  mais un tour de plus de la même boucle (±5 %, même tracé) s'enchaîne (marathon
  en deux tours) ; sinon la plus longue. La route sert si la trace manque, est
  inexploitable ou fait moins de la moitié de la route. Chaque choix est dit
  (`attrs["note"]`). Point illisible écarté ; tracé < 100 m, profil vide ou
  encodage illisible → `GpxError` (jamais une exception brute). Longueurs
  calculées en un passage vectoriel (24 000 traces < 0,5 s) et lecture mise en
  cache par la page (`_read_course`). Garde d'allure jugée à plat
  (`flat_equivalent_km`) : un KV à 17 min/km n'est pas une faute de frappe.
  Cartes de test réalistes : `tests/fixtures/gpx/make_cards.py` →
  `tests/test_gpx_cards.py` (et e2e). Temps visé : remis au défaut du parcours
  quand il change, lu en h:mm dès 18 km (`reading_distance`), refusé hors
  2:30-15:00/km (`implausible_target`). Allure = coût Minetti, gain en
  descente plafonné (× 0,88) ; météo Garmin en °F convertie. Stratégie
  `progressive` par défaut sur la page (départ +1,5 à 2,5 %, accélération sur le
  dernier cinquième, `progression_shape` selon la distance) : `even` seule donne
  la même allure à chaque km d'un parcours plat — c'était le « bracelet figé ».
  Le temps final vaut toujours le temps visé (renormalisé).
- **Comparaison de sorties** (`compare_logic`) : A = la plus ancienne, partout
  (grille, listes, cartes — la page réordonne la session). Allure corrigée =
  pente (`effort_factor`, le même que le plan d'allure) puis chaleur ; Riegel
  hors ±15 % de distance. Bloc d'avant = 6 semaines avant le jour (exclu),
  CTL/TSB lus en fin de veille sur `compute_pmc_series` (le TSB unique), nuits
  J−6…J. Une valeur absente d'un côté ne rend jamais une ligne « notable ». La
  grille lit la sélection Plotly dans `session_state` AVANT de se dessiner (pas
  de `st.rerun`, qui perdait un clic rapide) ; les traces de légende viennent
  APRÈS les traces cliquables (intercalées, elles décalaient le point renvoyé).
  Infobulle = `compare_logic.day_hover` (noms Garmin échappés : Plotly interprète
  le HTML du survol).
- **Météo d'activité** : `get_activity_weather(strict=True)` sous `st.cache_data`
  (un 429 n'est pas figé 24 h) ; un 404 = « pas de météo » (tapis), mis en cache
  avec un marqueur daté revérifié après un jour (`WEATHER_ABSENT_TTL`).
- **Coach IA — dates** : « Situation au » rejoue le contexte (sorties ≤ date,
  CTL/TSB de la série PMC ce jour-là, HRV/sommeil d'alors) et omet records et
  prédictions, qui n'existent qu'au présent ; aujourd'hui, `compute_tsb` comme
  partout. « Prochaine séance » ajoute le créneau au contexte et à la question,
  avec la séance que le plan (Run Coach, sinon Objectif) prévoit ce jour-là :
  séance → « ne la remplace pas » ; repos → « est-ce raisonnable ? » ; rien ou
  au-delà de l'horizon connu → séance libre.
- **Prompts MCP** (`garmin_mcp/prompts.py`) : builders purs (situation → texte) ;
  la situation lit le plan Run Coach en strict (panne → « inconnu », jamais « pas
  de plan ») et le prompt s'affiche même sans connexion Garmin.
- **Graphiques Plotly** : le frontend Streamlit force le fond gris des champs par
  dessus le template `gar` (même avec `theme=None`, qui en plus masque les
  graduations) → fond rendu transparent en CSS (`ui_theme`), pas par figure.
- **Séance du jour** : `next_session_logic.todays_session` + `forme_logic.parse_recovery`
  + `coach_logic.load_coach_context` + `goal_store.validated_sessions` — chemin
  unique Accueil (`pages/0_Accueil.py`) / Prochaine sortie / MCP. Priorité : Run Coach actif (même sans
  séance à venir) > plan Objectif validé (celui que la page Objectif envoie) >
  logique interne. La séance du jour du plan est ignorée si une course est déjà
  enregistrée aujourd'hui.

## Serveur MCP (`garmin_mcp/`)

- `insights.py` réutilise `app/` : mêmes chiffres que les pages par construction.
- Lecture seule par **liste blanche** (`get_*`, `count_*`, `download_*`, GET
  `connectapi`/`connectwebproxy` sans en-têtes ni corps). Ne pas revenir à une liste noire.
- Tokenstore **distinct** du dashboard (`GARMIN_TOKENSTORE_MCP`, défaut
  `~/.garminconnect-mcp` ; le dashboard hors Docker utilise `~/.garminconnect`) :
  deux processus sur un même refresh token se l'invalident.
- `mcp<2` : la v2 a renommé `FastMCP`.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pytest tests_ui/ -q
.venv/bin/python -m pytest tests_e2e/ -q
```

`pythonpath = app garmin_mcp` (cf. `pytest.ini`). Les tests ne touchent JAMAIS l'API Garmin :
`test_garmin_client.py` stubbe l'objet api (`FakeApi`) et isole le cache disque
dans `tmp_path` ; `tests_ui/` rend les vraies pages contre `tests_ui/fake_garmin.py`
(cache et `DATA_DIR` jetables, purgés entre tests) ; `tests_e2e/` démarre le vrai
dashboard (`tests_e2e/demo_server.py`, FakeGarmin, port libre, dossiers jetables) et
clique dans Chromium via Playwright — c'est la seule suite qui voit le CSS (un
élément recouvert refuse le clic). Tout changement d'en-tête, de navigation ou de
mise en page mobile doit la faire passer. Les formes des fixtures Garmin ont été validées contre l'API
réelle (juillet 2026) — les garder synchrones si l'API change.

## Règles de commit

- **Ne jamais commiter ni `git add` sans demande explicite** de l'utilisateur.
- **Commits conventionnels** : `type(scope): message` en minuscules, en anglais,
  < 72 caractères (`feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `style`).
- **Pas de signature Claude** dans les commits.

## Règles de documentation

- Mettre à jour le `README.md` à chaque changement d'envergure (page, service,
  variable d'env, auth, cache, déploiement). Pas de section décrivant une
  fonctionnalité supprimée.

## Variables d'environnement (`.env`)

```
GARMIN_EMAIL=           # pré-remplit le formulaire
GARMIN_PASSWORD=        # JAMAIS lu par le dashboard (test_connection.py / MCP seulement)
# GARMIN_TOKENSTORE=/app/.garmin
CACHE_TTL=3600
ORS_API_KEY=            # optionnel — page Prochaine sortie
# GARMIN_WRITE_ENABLED=true   # déjà posé par docker-compose.yml (port en loopback)
# DATA_DIR / STREAMS_CACHE_TTL / GARMIN_TOKENSTORE_MCP : voir README

# Production uniquement (docker-compose.prod.yml)
PUBLIC_DOMAIN=
ACME_EMAIL=
STREAMLIT_BROWSER_SERVER_ADDRESS=
BASIC_AUTH_USER=        # obligatoire : Caddy impose une authentification
BASIC_AUTH_HASH=''      # caddy hash-password ; quotes simples (le hash contient des $)
```

**Sécurité** : le mot de passe Garmin ne doit jamais atteindre un widget
(`value=`) ni servir de repli serveur ; `tests_ui/test_security_ui.py` le garde.
