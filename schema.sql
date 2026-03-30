-- =============================================================================
-- navla-aibrandmonitoring-2 — Full DB Schema
-- Run this in the Supabase SQL editor (once, in order).
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. customers
-- -----------------------------------------------------------------------------
CREATE TABLE customers (
  id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT        NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- -----------------------------------------------------------------------------
-- 2. projects
-- -----------------------------------------------------------------------------
CREATE TABLE projects (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id UUID        NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  name        TEXT        NOT NULL,
  language    TEXT        NOT NULL DEFAULT 'en',  -- ISO 639-1
  country     TEXT        NOT NULL DEFAULT 'us',  -- ISO alpha-2
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- -----------------------------------------------------------------------------
-- 3. keywords
-- -----------------------------------------------------------------------------
CREATE TABLE keywords (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id    UUID        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  keyword       TEXT        NOT NULL,
  cluster       TEXT,
  subcluster    TEXT,
  search_volume INT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- -----------------------------------------------------------------------------
-- 4. ai_questions
-- -----------------------------------------------------------------------------
CREATE TABLE ai_questions (
  id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  keyword_id UUID        REFERENCES keywords(id) ON DELETE SET NULL,
  question   TEXT        NOT NULL,
  intent     TEXT,
  tone       TEXT,
  source     TEXT        NOT NULL DEFAULT 'manual',  -- 'manual'|'serpapi_paa'|'csv_import'
  status     TEXT        NOT NULL DEFAULT 'active',  -- 'draft'|'active'
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- -----------------------------------------------------------------------------
-- 5. project_brands
-- -----------------------------------------------------------------------------
CREATE TABLE project_brands (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id    UUID        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  brand_name    TEXT        NOT NULL,
  is_competitor BOOLEAN     NOT NULL DEFAULT false,
  is_own_brand  BOOLEAN     NOT NULL DEFAULT false,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, brand_name)
);


-- -----------------------------------------------------------------------------
-- 6. project_schedules
-- -----------------------------------------------------------------------------
CREATE TABLE project_schedules (
  id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id     UUID        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  frequency      TEXT        NOT NULL,        -- 'weekly'|'biweekly'|'monthly'
  day_of_week    INT,                         -- 0=Mon … 6=Sun  (weekly/biweekly)
  day_of_month   INT,                         -- 1-28            (monthly)
  llms           TEXT[]      NOT NULL,        -- e.g. '{chatgpt,claude,gemini,perplexity,aio}'
  is_active      BOOLEAN     NOT NULL DEFAULT true,
  last_run_at    TIMESTAMPTZ,
  next_run_at    TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id)                         -- one schedule per project
);


-- -----------------------------------------------------------------------------
-- 7. runs
-- -----------------------------------------------------------------------------
CREATE TABLE runs (
  id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id          UUID        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at         TIMESTAMPTZ,
  status              TEXT        NOT NULL DEFAULT 'pending',
  -- 'pending'|'running'|'completed'|'failed'|'partial'
  triggered_by        TEXT        NOT NULL DEFAULT 'manual',  -- 'manual'|'scheduled'
  llms                TEXT[]      NOT NULL,
  error               TEXT,
  total_questions     INT,
  completed_questions INT         NOT NULL DEFAULT 0
);


-- -----------------------------------------------------------------------------
-- 8. run_workers
-- -----------------------------------------------------------------------------
CREATE TABLE run_workers (
  id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id         UUID        NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  ai_question_id UUID        NOT NULL REFERENCES ai_questions(id) ON DELETE CASCADE,
  llm            TEXT        NOT NULL,
  status         TEXT        NOT NULL DEFAULT 'pending',
  -- 'pending'|'running'|'completed'|'failed'
  started_at     TIMESTAMPTZ,
  finished_at    TIMESTAMPTZ,
  error          TEXT,
  attempt        INT         NOT NULL DEFAULT 1,
  UNIQUE (run_id, ai_question_id, llm)
);


-- -----------------------------------------------------------------------------
-- 9. ai_responses
-- -----------------------------------------------------------------------------
CREATE TABLE ai_responses (
  id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id         UUID        NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  run_worker_id  UUID        REFERENCES run_workers(id) ON DELETE SET NULL,
  ai_question_id UUID        REFERENCES ai_questions(id) ON DELETE SET NULL,
  llm            TEXT        NOT NULL,
  -- 'chatgpt'|'claude'|'gemini'|'perplexity'|'aio'
  model          TEXT,
  response_text  TEXT,
  run_date       DATE        NOT NULL DEFAULT CURRENT_DATE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- -----------------------------------------------------------------------------
-- 10. brand_mentions
-- -----------------------------------------------------------------------------
CREATE TABLE brand_mentions (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ai_response_id UUID NOT NULL REFERENCES ai_responses(id) ON DELETE CASCADE,
  brand_name     TEXT NOT NULL,  -- normalised by secondary LLM
  position       INT             -- ordinal of first mention (1-based)
);


-- -----------------------------------------------------------------------------
-- 11. source_mentions
-- -----------------------------------------------------------------------------
CREATE TABLE source_mentions (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ai_response_id UUID NOT NULL REFERENCES ai_responses(id) ON DELETE CASCADE,
  url            TEXT NOT NULL
  -- domain is derived at query time, not stored
);


-- -----------------------------------------------------------------------------
-- 12. user_customers
-- -----------------------------------------------------------------------------
CREATE TABLE user_customers (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL,    -- Supabase Auth user id
  customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  role        TEXT NOT NULL DEFAULT 'viewer',  -- 'admin'|'viewer'
  UNIQUE (user_id, customer_id)
);


-- =============================================================================
-- 13. Indexes
-- =============================================================================
CREATE INDEX idx_projects_customer_id        ON projects(customer_id);
CREATE INDEX idx_keywords_project_id         ON keywords(project_id);
CREATE INDEX idx_ai_questions_project_id     ON ai_questions(project_id);
CREATE INDEX idx_ai_questions_keyword_id     ON ai_questions(keyword_id);
CREATE INDEX idx_project_brands_project_id   ON project_brands(project_id);
CREATE INDEX idx_runs_project_id             ON runs(project_id);
CREATE INDEX idx_run_workers_run_id          ON run_workers(run_id);
CREATE INDEX idx_ai_responses_run_id         ON ai_responses(run_id);
CREATE INDEX idx_ai_responses_run_date       ON ai_responses(run_date);
CREATE INDEX idx_brand_mentions_response     ON brand_mentions(ai_response_id);
CREATE INDEX idx_source_mentions_response    ON source_mentions(ai_response_id);
CREATE INDEX idx_user_customers_user_id      ON user_customers(user_id);
CREATE INDEX idx_user_customers_customer_id  ON user_customers(customer_id);


-- =============================================================================
-- 14. View: v_brand_mentions_flat
-- One row = one brand citation
-- =============================================================================
CREATE VIEW v_brand_mentions_flat AS
SELECT
  p.id                              AS customer_id,
  ar.run_id,
  ar.run_date                       AS date,
  aq.question                       AS ai_question,
  k.keyword,
  k.cluster,
  k.subcluster,
  k.search_volume                   AS volume,
  ar.llm,
  ar.model,
  ar.ai_question_id,
  bm.id                             AS mention_id,
  bm.brand_name                     AS brand,
  bm.position,
  COALESCE(pb.is_competitor, false) AS is_competitor,
  COALESCE(pb.is_own_brand,  false) AS is_own_brand,
  pr.id                             AS project_id,
  pr.language,
  pr.country
FROM brand_mentions  bm
JOIN ai_responses    ar ON ar.id = bm.ai_response_id
JOIN ai_questions    aq ON aq.id = ar.ai_question_id
JOIN keywords        k  ON k.id  = aq.keyword_id
JOIN projects        pr ON pr.id = k.project_id
LEFT JOIN customers  p  ON p.id  = pr.customer_id
LEFT JOIN project_brands pb
       ON pb.project_id = pr.id
      AND LOWER(pb.brand_name) = LOWER(bm.brand_name);


-- =============================================================================
-- 15. View: v_source_mentions_flat
-- One row = one cited URL
-- =============================================================================
CREATE VIEW v_source_mentions_flat AS
SELECT
  p.id                              AS customer_id,
  ar.run_id,
  ar.run_date                       AS date,
  aq.question                       AS ai_question,
  k.keyword,
  k.cluster,
  k.subcluster,
  k.search_volume                   AS volume,
  ar.llm,
  ar.model,
  ar.ai_question_id,
  sm.id                             AS mention_id,
  sm.url,
  regexp_replace(
    regexp_replace(sm.url, '^https?://(www\.)?', ''),
    '/.*$', ''
  )                                 AS domain,
  pr.id                             AS project_id,
  pr.language,
  pr.country
FROM source_mentions sm
JOIN ai_responses    ar ON ar.id = sm.ai_response_id
JOIN ai_questions    aq ON aq.id = ar.ai_question_id
JOIN keywords        k  ON k.id  = aq.keyword_id
JOIN projects        pr ON pr.id = k.project_id
LEFT JOIN customers  p  ON p.id  = pr.customer_id;


-- =============================================================================
-- 16. View: v_ai_responses_flat
-- One row = one full LLM response
-- =============================================================================
CREATE VIEW v_ai_responses_flat AS
SELECT
  p.id                AS customer_id,
  ar.run_id,
  ar.run_date         AS date,
  aq.question         AS ai_question,
  k.keyword,
  k.cluster,
  k.subcluster,
  k.search_volume     AS volume,
  ar.llm,
  ar.model,
  ar.ai_question_id,
  ar.id               AS response_id,
  ar.response_text,
  pr.id               AS project_id,
  pr.language,
  pr.country
FROM ai_responses    ar
JOIN ai_questions    aq ON aq.id = ar.ai_question_id
JOIN keywords        k  ON k.id  = aq.keyword_id
JOIN projects        pr ON pr.id = k.project_id
LEFT JOIN customers  p  ON p.id  = pr.customer_id;
