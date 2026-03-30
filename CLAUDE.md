# navla-aibrandmonitoring-2 — CLAUDE.md

Riferimento completo per Claude Code. Aggiornato al termine di ogni step.

---

## Stack tecnologico

| Layer | Tecnologia |
|---|---|
| Frontend / App | Python 3.11+ + Streamlit (multi-pagina) |
| Grafici | Altair 5 |
| Database | Supabase (PostgreSQL) via SQLAlchemy 2 |
| Auth | Supabase Auth REST API + cookie refresh token (30 giorni) |
| Pipeline LLM | `ThreadPoolExecutor` con worker paralleli |
| Scheduling | Supabase pg_cron + Edge Functions |
| PAA / AI Overview | SerpApi (`google-search-results`) |
| Brand extraction | OpenAI `gpt-4o-mini` con output TOML |
| Cookie manager | `extra-streamlit-components` (`CookieManager`) |

---

## Struttura file

```
navla-aibrandmonitoring-2/
├── app.py                        ← entry point: login + home KPI
├── utils.py                      ← DB engine, auth, FilterState, fetch_*, CRUD
├── pipeline.py                   ← worker paralleli, chiamate API LLM, brand extraction
├── scheduler.py                  ← interfaccia Supabase Edge Functions  [step 8]
├── schema.sql                    ← DDL completo (tabelle + indici + view)
├── requirements.txt
├── CLAUDE.md                     ← questo file
├── assets/
│   └── logo.png
├── .streamlit/
│   └── secrets.toml              ← credenziali (NON committare)
└── pages/
    ├── 0_Clienti.py              ← CRUD customers + projects + user assignment
    ├── 1_Progetti.py             ← wizard 4-step creazione progetto
    ├── 2_Domande_e_Keyword.py    ← CRUD keyword + AI Questions + import CSV/Excel  [step 9]
    ├── 3_Scarico_Dati.py         ← avvio manuale + retry + scheduling UI  [step 7]
    ├── 4_Brand_AI.py             ← ranking brand, timeline, bubble chart, Share of Voice  [step 10]
    ├── 5_Fonti_AI.py             ← analisi URL / domini citati  [step 11]
    └── 6_Risposte_AI.py          ← risposte raw + analytics per domanda  [step 12]
```

---

## Schema DB (schema.sql)

### Tabelle (in ordine di creazione)

```sql
customers          id, name, created_at
projects           id, customer_id→customers, name, language, country, created_at
keywords           id, project_id→projects, keyword, cluster, subcluster, search_volume, created_at
ai_questions       id, project_id→projects, keyword_id→keywords(SET NULL), question,
                   intent, tone, source('manual'|'serpapi_paa'|'csv_import'),
                   status('draft'|'active'), created_at
project_brands     id, project_id→projects, brand_name, is_competitor, is_own_brand, created_at
                   UNIQUE(project_id, brand_name)
                   ⚠ is_own_brand: max 1 per progetto (validato in UI, non in DB)
project_schedules  id, project_id→projects UNIQUE, frequency('weekly'|'biweekly'|'monthly'),
                   day_of_week(0-6), day_of_month(1-28), llms TEXT[],
                   is_active, last_run_at, next_run_at, created_at
runs               id, project_id→projects, started_at, finished_at,
                   status('pending'|'running'|'completed'|'failed'|'partial'),
                   triggered_by('manual'|'scheduled'), llms TEXT[],
                   error, total_questions, completed_questions
run_workers        id, run_id→runs, ai_question_id→ai_questions,
                   llm, status('pending'|'running'|'completed'|'failed'),
                   started_at, finished_at, error, attempt
                   UNIQUE(run_id, ai_question_id, llm)
ai_responses       id, run_id→runs, run_worker_id→run_workers(SET NULL),
                   ai_question_id→ai_questions(SET NULL),
                   llm('chatgpt'|'claude'|'gemini'|'perplexity'|'aio'),
                   model, response_text, run_date DATE, created_at
brand_mentions     id, ai_response_id→ai_responses, brand_name, position
source_mentions    id, ai_response_id→ai_responses, url
user_customers     id, user_id UUID, customer_id→customers,
                   role('admin'|'viewer')
                   UNIQUE(user_id, customer_id)
```

### Indici

```sql
idx_projects_customer_id       projects(customer_id)
idx_keywords_project_id        keywords(project_id)
idx_ai_questions_project_id    ai_questions(project_id)
idx_ai_questions_keyword_id    ai_questions(keyword_id)
idx_project_brands_project_id  project_brands(project_id)
idx_runs_project_id            runs(project_id)
idx_run_workers_run_id         run_workers(run_id)
idx_ai_responses_run_id        ai_responses(run_id)
idx_ai_responses_run_date      ai_responses(run_date)
idx_brand_mentions_response    brand_mentions(ai_response_id)
idx_source_mentions_response   source_mentions(ai_response_id)
idx_user_customers_user_id     user_customers(user_id)
idx_user_customers_customer_id user_customers(customer_id)
```

### View (usate da Streamlit — mai JOIN sulle tabelle raw)

```
v_brand_mentions_flat   customer_id, run_id, date, ai_question, keyword, cluster,
                        subcluster, volume, llm, model, ai_question_id,
                        mention_id, brand, position, is_competitor, is_own_brand,
                        project_id, language, country

v_source_mentions_flat  customer_id, run_id, date, ai_question, keyword, cluster,
                        subcluster, volume, llm, model, ai_question_id,
                        mention_id, url, domain (regex-calcolato),
                        project_id, language, country

v_ai_responses_flat     customer_id, run_id, date, ai_question, keyword, cluster,
                        subcluster, volume, llm, model, ai_question_id,
                        response_id, response_text, project_id, language, country
```

---

## Secrets (.streamlit/secrets.toml)

```toml
[db]
url = "postgresql://..."          # SQLAlchemy connection string

[supabase]
project_url = "https://....supabase.co"
anon_key    = "..."               # Usato per Auth REST API
service_role_key = "..."          # Usato per Admin API (user lookup)

[api_keys]
openai      = "..."
anthropic   = "..."
google      = "..."
perplexity  = "..."
serpapi     = "..."

[pipeline]
max_workers              = 4
brand_extraction_model   = "gpt-4o-mini"
request_delay_seconds    = 1
```

---

## Convenzioni codice

### Pattern obbligatori

```python
# Inizio di ogni pagina
cookie_manager = get_cookie_manager()   # PRIMA di qualsiasi st.*
require_login(cookie_manager)
render_sidebar(cookie_manager)
is_admin = st.session_state.get("role") == "admin"
project_id = st.session_state.get("project_id")
customer_id = st.session_state.get("customer_id")
```

### Query DB

```python
# Letture — SEMPRE @st.cache_data
@st.cache_data(ttl=300)          # analytics
@st.cache_data(ttl=60)           # CRUD reference data
@st.cache_data(ttl=15)           # runs / run_workers

def fetch_qualcosa(filters: FilterState) -> pd.DataFrame:
    where, params = build_where_clause(filters)
    return run_query(f"SELECT col1, col2 FROM v_brand_mentions_flat WHERE {where}", params)

# Scritture — SEMPRE engine.begin()
with get_engine().begin() as conn:
    conn.execute(text("INSERT INTO ..."), {...})
```

### SQL placeholder style

- `%(name)s` → `pd.read_sql` / `run_query`
- `:name` → SQLAlchemy `text()` con `engine.begin()`

### Regole invariabili

- **Mai `SELECT *`** (nemmeno sulle view — elencare le colonne)
- **Mai JOIN sulle tabelle raw** nelle pagine di analisi — usare solo le 3 view
- Nomi funzioni: `fetch_*` (query con cache), `render_*` (componenti UI), `_*` (privato)
- Altair: sempre `use_container_width=True`, tooltip completi, nessun colore hardcoded
- Cache clear dopo ogni scrittura: `fetch_qualcosa.clear()` o `st.cache_data.clear()`
- Delete sempre con doppia conferma via `st.session_state["confirm_del_*"]`

### FilterState

```python
@dataclass(frozen=True)
class FilterState:
    project_id:  Optional[str]
    customer_id: Optional[str]
    date_range:  Optional[Tuple[date, date]]
    llms:        Tuple[str, ...] = ()
    clusters:    Tuple[str, ...] = ()

where, params = build_where_clause(filters)
# → ("project_id = %(project_id)s AND date BETWEEN ...", {"project_id": "...", ...})
```

---

## Dettaglio file implementati

### utils.py

**Funzioni principali:**

| Funzione | TTL | Note |
|---|---|---|
| `get_engine()` | `@cache_resource` | SQLAlchemy engine, pool_pre_ping |
| `get_cookie_manager()` | — | Wrapper `extra_streamlit_components.CookieManager(key="cm")` |
| `login(email, password, cm)` | — | POST `/auth/v1/token?grant_type=password`; salva refresh in cookie |
| `logout(cm)` | — | POST `/auth/v1/logout`; cancella cookie e session_state |
| `auto_login_from_cookie(cm)` | — | POST `/auth/v1/token?grant_type=refresh_token`; ruota il token |
| `require_login(cm)` | — | Guard: mostra form login e `st.stop()` se non autenticato |
| `render_sidebar(cm)` | — | Selettori customer/project, date range, LLM, cluster → FilterState |
| `build_where_clause(filters)` | — | Genera `(WHERE sql, params dict)` per `%(name)s` |
| `run_query(sql, params)` | — | `pd.read_sql` con engine |

**fetch_* (cache 60s — dati CRUD):**
`fetch_customers_all`, `fetch_projects(customer_id)`, `fetch_project(project_id)`,
`fetch_keywords(project_id)`, `fetch_ai_questions(project_id, status?)`,
`fetch_project_brands(project_id)`, `fetch_project_schedule(project_id)`,
`fetch_clusters(project_id)`

**fetch_* (cache 15s — runs):**
`fetch_runs(project_id)`, `fetch_run_workers(run_id)`

**fetch_* (cache 300s — analytics):**
`fetch_brand_mentions(filters)`, `fetch_source_mentions(filters)`, `fetch_ai_responses_flat(filters)`

**CRUD:**
`create_customer`, `update_customer`, `delete_customer`
`create_project`, `update_project`, `delete_project`
`insert_keywords`, `delete_keyword`
`insert_ai_questions`, `update_ai_question_status`, `delete_ai_question`
`upsert_project_brands`, `delete_project_brand`
`upsert_project_schedule`, `set_schedule_active`
`assign_user_to_customer`

---

### app.py

Entry point Streamlit. Ordine di inizializzazione:
1. `st.set_page_config()` (prima di tutto)
2. `cookie_manager = get_cookie_manager()`
3. `require_login(cookie_manager)`
4. `render_sidebar(cookie_manager)` → `FilterState`

**Contenuto home:**
- **KPI row (4 colonne):** brand mentions, risposte LLM, brand unici, run completati
- **Bar chart Altair:** citazioni per LLM
- **Bar chart Altair:** top 10 brand del progetto
- **Line chart Altair:** timeline citazioni per giorno × LLM
- **Metric pair:** brand proprio vs competitor (se taggati)
- **Tabella:** ultimi 10 run con `st.column_config`

---

### pipeline.py

**LLM callers** (tutte restituiscono `(text: str, sources: list[str], model_name: str)`):

| Funzione | Endpoint | Timeout | Note |
|---|---|---|---|
| `_call_chatgpt(question, country)` | `/v1/responses` → fallback `/v1/chat/completions` | 60s | Fonti da `annotations[].url` (Responses API) |
| `_call_claude(question)` | `/v1/messages` | 90s | `anthropic-beta: web-search-2025-03-05`; fonti via regex |
| `_call_gemini(question, country, language)` | `/v1beta/models/{model}:generateContent` | 60s | Cascade `2.5-flash→2.0-flash→1.5-flash` (skip 404); redirect Vertex risolti con `requests.head` |
| `_call_perplexity(question)` | `/chat/completions` | 60s | `sonar-pro`; fonti da `citations[]` top-level |
| `_call_aio(question, country, language)` | SerpApi `engine=google` | — | `no_cache=False`; testo da `aio.get("text")` o join snippet da `references[]`; fonti da `references[].link`; se `ai_overview` assente → `(None, [], "google_aio")` |

**Brand extraction:**
`_extract_brands(response_text)` → chiama `gpt-4o-mini`, strip markdown fence, parsing TOML
con `tomllib` (3.11+) o `tomli`; errore → log + `[]` (non blocca il worker).

**Chiave mancante** → `"DISABLED"`. **Errore API** → `"ERROR: {msg}"`.
`_is_valid_response(text)` → False se None / "DISABLED" / starts with "ERROR:".

**API pubblica:**

```python
run_id = start_run(
    project_id: str,
    llms: list[str],
    triggered_by: str = "manual",          # 'manual' | 'scheduled'
    progress_callback: Callable[[int, int], None] | None = None,
) -> str

retry_failed_workers(
    run_id: str,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None
```

**Flusso `start_run`:**
1. Carica `ai_questions` attive del progetto
2. Crea record `runs` (status=running)
3. Crea record `run_workers` per ogni (question × llm) (status=pending)
4. `ThreadPoolExecutor(max_workers)` → `_worker()` per ogni coppia
5. Ogni `_worker`: mark running → call LLM → insert ai_response → insert sources → extract brands → insert brand_mentions → mark completed/failed → increment completed_questions atomico
6. `_db_finalize_run`: `completed` se tutti ok, `partial` se almeno un fallito

**`retry_failed_workers`:**
Crea nuovi `run_workers` con `attempt = precedente + 1`, rimette `run.status = 'running'`,
ri-esegue solo i falliti, finalizza.

---

### pages/0_Clienti.py

**5 sezioni:**

1. **Lista clienti** — `fetch_customers_all()` + conteggio progetti; form "Nuovo cliente" (admin, expander)
2. **Modifica/Elimina** (admin) — selectbox → rename form + delete con doppia conferma
3. **Progetti per cliente** — tabella con colonne: nome, lingua, paese, domande attive, data; pulsante "Crea nuovo progetto" → `switch_page(1_Progetti.py)` (pulisce stato `wiz1_*`); delete progetto con conferma
4. **Utenti assegnati** (admin) — tabella `user_customers`; form "Assegna utente" con lookup email via Supabase Auth Admin API (`GET /auth/v1/admin/users`, `service_role_key`)
5. **Gestione brand per progetto** (admin) — selectbox progetto (tutti i clienti); `st.data_editor(num_rows="dynamic")` con colonne `brand_name`, `is_competitor`, `is_own_brand`; validazione: max 1 riga con `is_own_brand=True` per progetto; salvataggio con DELETE + re-insert via `upsert_project_brands()`

Viewer: vede solo il proprio `customer_id`.

---

### pages/1_Progetti.py

**Wizard 4-step** (admin only). Stato in `session_state` con prefisso `wiz1_`.
Helper: `_get(key)`, `_set(key, val)`, `_reset()`.

| Step | Azione | Helper |
|---|---|---|
| 1 | Nome, lingua, paese → `create_project()` | — |
| 2A | Upload CSV/Excel (keyword+domande) → preview → save | `_save_percorso_a()` |
| 2B | Upload keyword → `_fetch_paa()` SerpApi → `st.data_editor` checkbox → save selected | `_save_percorso_b()` |
| 2C | Crea progetto senza keyword | — |
| 3 | Brand input (riga/virgola) → checkbox competitor → `upsert_project_brands()` — saltabile | `_parse_brands()` |
| 4 | Frequenza, giorno, LLM → `upsert_project_schedule()` — saltabile | `_calc_next_run()` |
| 5 | Conferma + link a Domande e Keyword | `_finalize()` → `st.cache_data.clear()` |

`_fetch_paa(keywords, language, country)` — `requests.get("https://serpapi.com/search.json", params={engine,q,api_key,gl,hl})`, timeout 15s; PAA da `related_questions[].question`, max 4 per keyword con dedup; errore HTTP → `st.warning` + continua; restituisce DataFrame con colonne `keyword, question`.

`_save_percorso_a/b` — usa `run_query` diretto (no cache) per leggere ID keyword subito dopo l'insert.

---

### pages/3_Scarico_Dati.py

**4 sezioni:**

1. **Avvio manuale** — multiselect LLM, bottone "Avvia run" → `pipeline.start_run()` con `st.progress` callback; warning se nessuna domanda attiva
2. **Storico run** — `st.data_editor` (admin) con colonna checkbox `_sel` + colonne dati disabilitate; pulsante "Elimina N run selezionati" con doppia conferma → `DELETE FROM runs WHERE id::text = ANY(:ids)` (cascade su run_workers, ai_responses, brand_mentions, source_mentions); viewer vede `st.dataframe` read-only
3. **Retry worker falliti** — selectbox filtrata su run `partial`/`failed` → tabella worker falliti → `pipeline.retry_failed_workers()` con progress bar; `fetch_runs.clear()` + `fetch_run_workers.clear()` al termine
4. **Pianificazione automatica** (admin only) — mostra schedule corrente con toggle attiva/disattiva (`set_schedule_active`); form per frequenza/giorno/LLM → `upsert_project_schedule()` con calcolo `_calc_next_run()`

---

### scheduler.py

Script eseguibile standalone + HTTP webhook server per Supabase Edge Functions.

**Modalità di invocazione:**
```bash
python scheduler.py               # esegue tutti i run in scadenza
python scheduler.py run --project <UUID> --llms chatgpt gemini
python scheduler.py serve --port 9000 --token <secret>
```

**Funzioni pubbliche:**

| Funzione | Descrizione |
|---|---|
| `get_due_projects()` | Query `project_schedules` WHERE `is_active AND next_run_at <= NOW()`; ritorna lista di dict |
| `run_single_project(project_id, llms)` | Chiama `pipeline.start_run(..., triggered_by="scheduled")`; ritorna `run_id` o `None` |
| `run_due_schedules()` | Orchestrazione principale: `get_due_projects` → loop → `run_single_project` → `_update_schedule_timestamps`; ritorna summary dict |
| `serve(port, token)` | HTTP server (`http.server`); endpoint `POST /run-due`, `GET /health`; auth opzionale con Bearer token |

**Secrets bootstrap**: prima di importare `pipeline`/`utils`, legge `.streamlit/secrets.toml` con `tomllib` e inietta un `_FlatSecrets` dict-like in `st.secrets`, così `st.secrets["db"]["url"]` funziona fuori dal contesto Streamlit.

**`_update_schedule_timestamps`**: `UPDATE project_schedules SET last_run_at = NOW(), next_run_at = :next WHERE project_id = :pid`

**Exit code**: 0 se tutti i run riusciti, 1 se almeno uno fallito.

---

### pages/2_Domande_e_Keyword.py

**3 sezioni:**

1. **Keyword** — tabella `fetch_keywords(project_id)` con cluster/sub-cluster/volume; (admin) form aggiunta singola + import CSV/Excel (colonne: `keyword`, `cluster`, `subcluster`, `search_volume`) + delete con doppia conferma
2. **AI Questions** — tabella `fetch_ai_questions(project_id)` con filtri per cluster e stato; (admin) form aggiunta manuale + import CSV/Excel (colonne: `question`, `keyword`, `intent`, `tone`, `status`) con matching keyword per testo esatto; gestione stato singola domanda / bulk attiva-tutte / draft-tutte; delete con doppia conferma
3. **Esporta** — `st.download_button` CSV per keyword e per domande (tutti i ruoli)

**Note implementative:**
- Keyword enrichment: join in-memory tra `q_df["keyword_id"]` e `kw_df["id"]` per mostrare testo e cluster nella tabella domande
- Import domande: abbinamento keyword per testo esatto lowercase; keyword non trovate → `keyword_id = NULL` + warning utente; `source` impostato a `"csv_import"`
- Bulk status change: loop su `q_df["id"]` con chiamata singola `update_ai_question_status` per ogni riga (nessuna funzione bulk in utils)
- Delete keyword: `fetch_ai_questions.clear()` oltre a `fetch_keywords.clear()` perché le domande associate vengono rimosse in cascade

---

### pages/4_Brand_AI.py

**5 sezioni + KPI row espansa:**

1. **KPI row (5 colonne)** — citazioni totali, brand unici, brand proprio (`is_own_brand=True`), competitor, **Share of Voice** (own/totale×100 con delta pp vs run precedente se ≥2 date disponibili; mostra `—` se nessun brand marcato come proprio)
2. **Ranking brand** — bar chart orizzontale top-N con slider; radio split: Nessuno / LLM (stacked) / Competitor vs proprio; expander con tabella ranking completa
3. **Trend nel tempo** — line chart con granularità Giorno/Settimana/Mese; raggruppamento per LLM / Brand top 10 / Competitor vs proprio
4. **Bubble chart brand × LLM** — cerchi dimensionati per citazioni, asse X = LLM, asse Y = brand ordinati per totale
5. **Analisi posizione** — bar chart posizione media (se colonna `position` disponibile) + boxplot distribuzione posizioni top 10 brand
6. **Heatmap brand × cluster** — `mark_rect` con schema colori `blues`; top-N brand configurabile

**Note:**
- KPI "Brand proprio" usa `is_own_brand` (non `~is_competitor`)
- SoV delta calcolato confrontando ultima data vs penultima data in `brand_df["date"]`
- Tutti i grafici usano `use_container_width=True` e `tooltip` completi
- Granularità settimana/mese calcolata con `dt.to_period().apply(p.start_time)`
- Position analysis e heatmap si nascondono con `st.info` se dati assenti (guard su `.isna().all()`)

---

## Avanzamento implementazione

| Step | File | Stato |
|---|---|---|
| 1 | `schema.sql` | ✅ Completato — eseguito su Supabase |
| 2 | `utils.py` + `requirements.txt` + `.streamlit/secrets.toml` | ✅ Completato |
| 3 | `app.py` | ✅ Completato |
| 4 | `pages/1_Progetti.py` | ✅ Completato |
| 5 | `pages/0_Clienti.py` | ✅ Completato |
| 6 | `pipeline.py` | ✅ Completato |
| 7 | `pages/3_Scarico_Dati.py` | ✅ Completato |
| 8 | `scheduler.py` | ✅ Completato |
| 9 | `pages/2_Domande_e_Keyword.py` | ✅ Completato |
| 10 | `pages/4_Brand_AI.py` (ex `4_Analisi_Brand.py`) | ✅ Completato |
| 11 | `pages/5_Fonti_AI.py` | ✅ Completato |
| 12 | `pages/6_Risposte_AI.py` | ✅ Completato |

---

### pages/5_Fonti_AI.py

**5 sezioni (usa `v_source_mentions_flat`):**

1. **KPI row** — citazioni fonte totali, URL unici, domini unici
2. **Ranking domini** — bar chart orizzontale top-N; split per LLM; expander tabella completa. Fallback: se `domain` assente usa `url`
3. **Trend fonti nel tempo** — line chart granularità Giorno/Settimana/Mese, top-N domini configurabile
4. **Bubble chart dominio × LLM** — stessa struttura di `4_Brand_AI.py`
5. **Heatmap dominio × cluster** — schema colori `greens`
6. **Elenco URL** — tabella searchable con `st.column_config.LinkColumn` per URL cliccabili; cerca su url e domain

---

### pages/6_Risposte_AI.py

**5 sezioni (usa `v_ai_responses_flat` + `v_brand_mentions_flat` per co-analisi):**

1. **KPI row** — risposte totali, domande distinte, LLM attivi, range date
2. **Heatmap copertura** — `mark_rect` domanda × LLM con conteggio risposte (schema `blues`)
3. **Heatmap brand per domanda** — `mark_rect` domanda × brand (schema `oranges`); mostrata solo se `brand_df` non vuoto
4. **Distribuzione per LLM** — bar chart conteggio + line chart timeline side-by-side
5. **Visualizzatore risposte** — filtri per domanda/LLM/data; fino a 20 card espandibili con testo markdown completo; gestione stati DISABLED/ERROR; mostra brand citati inline se presenti in `brand_df`
6. **Tabella riepilogativa** — group-by domanda × LLM × keyword × cluster con conteggio risposte e data ultima risposta

---

## Note implementative importanti

- `get_cookie_manager()` deve essere chiamato **prima** di qualsiasi altro `st.*` dopo `st.set_page_config()`.
- `_save_percorso_a/b` in `1_Progetti.py` usano `run_query` diretto (bypass cache) per leggere gli UUID delle keyword appena inserite.
- `completed_questions` incrementato con `UPDATE … = completed_questions + 1` per atomicità in contesto multi-thread.
- Vertex AI redirect (`vertexaisearch.cloud.google.com`) risolti con `requests.head(allow_redirects=True, timeout=5)`.
- TOML brand extraction: strip markdown fences prima del parsing. Errore di parsing → `[]`, il worker continua.
- `tomllib.loads()` vuole sempre una `str`, mai `bytes` — non usare `.encode()`.
- Tutte le pagine di analisi usano **solo** le 3 view (`v_brand_mentions_flat`, `v_source_mentions_flat`, `v_ai_responses_flat`).
- La colonna `domain` in `v_source_mentions_flat` è calcolata via `regexp_replace` in PostgreSQL, non salvata.
- `is_own_brand` in `project_brands`: la constraint "max 1 per progetto" è validata solo in UI (sezione 5 di `0_Clienti.py`), non a livello DB. Non aggiungere un UNIQUE parziale senza allinearlo alla logica di salvataggio (DELETE+re-insert).
- Delete run in `3_Scarico_Dati.py`: usa `id::text = ANY(:ids)` per evitare il type mismatch UUID vs text in PostgreSQL.
- `_call_aio`: il modello restituisce `"google_aio"` come model_name in tutti i casi (DISABLED incluso). Le fonti vengono da `ai_overview["references"][].link`, il testo da `ai_overview.get("text")` con fallback a join degli snippet.
- `_fetch_paa` in `1_Progetti.py` usa `requests.get` diretto (non la classe `GoogleSearch`) — nessuna dipendenza dal package `serpapi` in questa funzione.
