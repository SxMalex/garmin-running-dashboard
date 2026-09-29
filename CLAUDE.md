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
jour-de-l'année via `comparatif_logic.py`), `9_Objectif` (course datée → plan
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
  `ui_theme.esc` (une ligne vide rouvrirait le Markdown) ; en Markdown, par
  `formatting.md_escape`, qui met aussi « : » en entité `&#58;` (un backslash ne
  neutralise pas les icônes `:material/x:` de Streamlit — vérifié au navigateur).
  Contrastes gardés par `tests_ui/test_theme_ui.py` : `weak_colours` lit le code
  (ast, f-strings comprises) et refuse toute couleur en dur hors tokens
  `chart_theme` ou sous 3:1 — hex 3/4/6/8, `rgb/rgba/hsl` en toute casse, et
  toute couleur calculée. Couleurs de séries : `ct.rgba(token, alpha)`.
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
- **Session partagée par le process** (`shared_session` / `adopt_session` /
  `end_session`) : UN objet Garmin pour tous les onglets (mono-utilisateur).
  0.3.6 réécrit `garmin_tokens.json` à chaque rafraîchissement
  (`client._tokenstore_path`) : un objet par onglet recréait le fichier après une
  déconnexion. La déconnexion neutralise l'objet SANS attendre (chemin puis
  jetons à None), puis efface le tokenstore ; un rafraîchissement en vol efface
  à son retour ses jetons ET le fichier qu'il a pu réécrire (s'il porte un de
  ses jetons — jamais celui d'une reconnexion faite entre-temps). Limite : le
  jeton n'est pas révoqué côté Garmin (la bibliothèque ne l'expose pas), il
  n'existe simplement plus nulle part côté dashboard. `_refresh_session` est enveloppé : un seul
  à la fois, et un seul par expiration. Aucun appel réseau sous `_SESSION_LOCK` ;
  reprise sous `_RESUME_LOCK`, pas rejouée avant `RESUME_RETRY_S` après un échec.
  Les pages lisent la session à chaque run (`ui_helpers.get_session_api`).
- **Identifiant d'athlète** : `resolve_athlete_id` → (id, fiable). 3 essais,
  puis l'id déjà connu du compte (`DATA_DIR/athlete_ids.json`, écrit de façon
  atomique, partagé avec le MCP), sinon md5 du display_name marqué NON fiable,
  retenté en arrière-plan 1×/min. Sous un id non fiable : `9_Objectif` n'écrit
  rien, les pages ne lisent pas le plan (`ui_helpers.validated_plan_sessions`,
  avis affiché), le MCP reconstruit son client au plus 1×/min.
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
  Le **TSB a une seule définition** : `tsb = round(ctl, 1) − round(atl, 1)` en fin
  de journée (la soustraction des deux chiffres affichés), posée dans
  `compute_pmc_series()` et simplement relue par `compute_tsb()`, `tab_charge`,
  le Comparatif et le Calendrier — ailleurs, seulement recomposée à partir des
  MÊMES CTL/ATL arrondis de la série (Calendrier, Coach IA « situation au »), ce
  qui donne le même chiffre (sinon 40,04 /
  30,05 donnait +9,9 en haut de `3_Forme` et 10,0 dans l'onglet Charge). L'écart
  au `ctl − atl` brut reste ≤ 0,1 sur la courbe. Libellé de fraîcheur des
  métriques : `forme_logic.tsb_metric_delta` (seuils `TSB_FRESH` / `TSB_FATIGUE`),
  le même partout. Une variante « fraîcheur d'avant-séance » ferait réapparaître
  deux TSB sur la même page. Test à la limite d'arrondi : `tests/pmc_edge.py`.
- **Heatmap** : logique pure dans `heatmap_logic.py`, testée.
- **Coach Garmin** : `coach_logic.py`, testé. Toute page qui annonce une séance
  passe par `ui_helpers.cached_coach_context()` puis
  `next_session_logic.todays_session` (cf. « Séance du jour ») — sinon elle
  affiche une reco divergente de celle de la montre. `merge_coach_into_recommendation()`,
  appelée par `todays_session`,
  fait piloter la séance par le plan adaptatif tout en respectant le contrat de
  `recommend_session` — ne pas casser ce contrat, la page et le générateur ORS en
  dépendent. On **avertit** sans réécrire la séance quand la récup est dégradée :
  réécrire ferait diverger le dashboard de la montre.
- **Comparatif annuel** : `comparatif_logic.py`, testé. `aligned_doy()` corrige les
  années bissextiles (le 1er mars vaut 60 partout) — c'est ce qui garantit que les
  courbes des années se superposent sur le bon jour. Le face-à-face du jour
  (`day_comparison(df, date)`) filtre sur le vrai (mois, jour) : le 29 février ne
  retombe pas sur le 28.
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
  (`DECOUPLING_TREND_MAX_RUNS`) et s'arrêtent au premier refus Garmin : seuls
  404/410 (activité supprimée) sautent une sortie
  (`garmin_client.skippable_activity_error`), et deux d'affilée arrêtent
  (`MAX_SKIPPED_IN_ROW` : c'est alors l'endpoint qui manque) ; 400, 401/403
  (jetons révoqués, blocage), 429, 5xx et réseau arrêtent la boucle. Un refus
  strict reste un appel réel : cooldown aussi sur le chemin d'erreur.
- **Plan** (`race_plan_logic.py`) : déterministe ; semaines calendaires ; allures
  de prescription = course récente > prédiction Garmin × 1,03 > entraînements —
  JAMAIS `reference_threshold_sec` (réservé au TSS) ; volume annoncé = volume
  prescrit (±10 %) — semaine 1 entamée comprise : elle annonce ce qui reste
  prescrit et la progression démarre à la première semaine complète ; renfo
  jamais la veille d'une séance clé ni le jour de la sortie longue, arrêt J-9
  (règles sourcées dans `SOURCES`).
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
  Réconciliation d'une séance retrouvée (journal perdu) : par ÉTIQUETTE (un
  créneau renommé reste le même), contenu relu (`get_workout`) et comparé
  (`workout_export.workout_content`, nombres à 1e-3 près : Garmin relit en
  float32) — différent → `UNVERIFIED_FINGERPRINT`,
  compté périmé et non planifié ; copies étiquetées (« Copie de … ») : la copie
  vérifiée est retenue, les autres signalées. Noms lus dans Garmin affichés
  via `md_escape`. Planification cherchée à ±7 jours
  (`find_schedule`, séance déplacée dans Garmin Connect : pas de doublon). Chaque
  appel d'écriture ou de listing est suivi du cooldown (`_paced`, même sur
  erreur). La garde Run Coach s'affiche sur une lecture fraîche de < 60 s mais
  est RELUE en strict au clic « Envoyer ». Jetons garth hérités
  (`oauth1/2_token.json`) purgés à la reprise, côté dashboard et MCP.
- **Modes Light/Pro** (`ui_mode.py`) : état hors clés de widget ; les réglages
  Pro ne portent que sur les seuils physio, jamais sur CTL/ATL (un seul TSB).
- **Intensité** (`activities_logic`) : IF = allure seuil du TSS
  (`reference_threshold_sec`) ÷ allure. TSS = `next_session_logic.activity_tss`,
  la formule par activité que `daily_tss` agrège — exactement la charge du PMC
  (plafond, courses sans allure exclues, sport croisé). Sur une vue filtrée,
  `enrich(filtered, seuil, history=df)` : le facteur du sport croisé se calibre
  sur l'historique complet (calibré sur le filtre, un wing valait 50 au lieu de
  200). Calcul par position : un index dupliqué ne mélange rien. Zones : < 0,78
  récup … > 1,03 VMA.
- **Projections** (`forecast_logic`) : tendance Theil-Sen sur 8 semaines, départ
  ancré sur la médiane des 7 derniers jours (sinon une saison en V projetait une
  régression alors que la forme remonte), gains amortis (τ 75 j), plafonds
  ±2 %/mois (temps) et ±1 pt/mois (VO2max), bande ≥ ±1 % / ±1 pt ; aucune
  projection sous 8 points ou 4 semaines. Toujours affichée comme estimation.
- **Graphiques Plotly** : le frontend Streamlit force le fond gris des champs par
  dessus le template `gar` (même avec `theme=None`, qui en plus masque les
  graduations) → fond rendu transparent en CSS (`ui_theme`), pas par figure.
- **Séance du jour** : `next_session_logic.todays_session` + `forme_logic.parse_recovery`
  + `coach_logic.load_coach_context` + `goal_store.validated_sessions` — chemin
  unique Accueil (`pages/0_Accueil.py`) / Prochaine sortie / MCP. Priorité : Run Coach actif (même sans
  séance à venir) > plan Objectif validé (celui que la page Objectif envoie) >
  logique interne. La séance du jour du plan est ignorée si une course est déjà
  enregistrée aujourd'hui. Moins de `MIN_RUNS_FOR_SESSION` (3) courses datées :
  pas de séance (None, jamais d'exception). État Run Coach inconnu (Garmin muet,
  `coach_logic.COACH_UNKNOWN`, jamais mis en cache ; UNE lecture
  `get_training_plans(strict=True, use_cache=True)` — la garde d'écriture, elle,
  lit toujours frais) — seule une panne Garmin/réseau (`is_garmin_failure`)
  devient « inconnu », un bug de lecture remonte : ni Run Coach ni le plan
  Objectif ne sont annoncés à sa place — séance du dashboard avec un avis
  (Accueil, Prochaine sortie, Coach IA, MCP `coach_plan.status = "unknown"`).
  Semaine de l'Accueil : un renfo ne marque pas « fait » un jour où une course
  est prévue ; allure et FC du mois = `home_logic.run_totals` (période seule,
  allure = temps ÷ distance, FC pondérée par la durée).

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
