-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Drop tables for a clean setup
DROP TABLE IF EXISTS m60_level CASCADE;
DROP TABLE IF EXISTS mom_level CASCADE;
DROP TABLE IF EXISTS industry_performance CASCADE;
DROP TABLE IF EXISTS nl_sql_pairs CASCADE;
DROP TABLE IF EXISTS nlpsql_schema_embeddings CASCADE;
DROP TABLE IF EXISTS nlpsql_query_history CASCADE;

CREATE TABLE m60_level (
    sbu INTEGER,
    sbu_name TEXT,
    zone TEXT,
    zone_name TEXT,
    region TEXT,
    region_name TEXT,
    sa TEXT,
    salesarea_name TEXT,
    product NUMERIC(18,4),
    productname TEXT,
    uom TEXT,
    invoice_dt INTEGER,
    target_qty_kl INTEGER,
    target_qty_tmt NUMERIC(18,4),
    fiscal_year TEXT,
    cur_fiscal_year INTEGER,
    orgsbucd NUMERIC(18,4),
    orgsbuname TEXT,
    orgzonecd TEXT,
    orgzonename TEXT,
    orgrocd TEXT,
    orgroname TEXT,
    orgsacd NUMERIC(18,4),
    orgsaname TEXT,
    productcode NUMERIC(18,4),
    materialgroupname TEXT,
    curfiscalyear NUMERIC(18,4),
    fiscalyear TEXT,
    yearmonth NUMERIC(18,4),
    netweight_uom TEXT,
    netweight_kg NUMERIC(18,4),
    netweight_tmt NUMERIC(18,4),
    total_days_till_presentday INTEGER,
    number_of_sundays_till_presentday INTEGER,
    target_round NUMERIC(18,4),
    actual_round NUMERIC(18,4),
    finalsum NUMERIC(18,4),
    finalactualsum NUMERIC(18,4),
    maxpendingdays INTEGER,
    working_days_till_presentday_withoutsundays INTEGER,
    rate_per_day_required_mmt NUMERIC(18,4),
    rate_per_day_current_mmt INTEGER,
    total_days_in_fy INTEGER,
    pending_days INTEGER,
    month_year INTEGER,
    month_name TEXT,
    fy_month INTEGER,
    year_monthname DATE,
    target_quantity_tmtt NUMERIC(18,4),
    prediction_value NUMERIC(18,4),
    act_tgt_achievement NUMERIC(18,4),
    zone_region_achievement NUMERIC(18,4),
    product_achievement NUMERIC(18,4),
    engine_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_m60_level_sbu ON m60_level (sbu_name);

CREATE TABLE mom_level (
    orgsbucd INTEGER,
    sbu_name TEXT,
    orgzonecd TEXT,
    zone_name TEXT,
    orgrocd TEXT,
    region_name TEXT,
    orgsacd INTEGER,
    salesarea_name TEXT,
    productcode INTEGER,
    productname TEXT,
    curfiscalyear INTEGER,
    fiscalyear TEXT,
    yearmonth INTEGER,
    netweight_uom TEXT,
    netweight_kg NUMERIC(18,4),
    netweight_tmt NUMERIC(18,4),
    fiscal_year TEXT,
    engine_id TEXT,
    month_name TEXT,
    day_id DATE,
    distribution_channel_cd INTEGER,
    material_group_cd INTEGER,
    plant_cd INTEGER
);

CREATE INDEX IF NOT EXISTS idx_mom_level_sbu ON mom_level (sbu_name);

CREATE TABLE industry_performance (
    prod1_1 TEXT,
    category TEXT,
    sbu_name TEXT,
    statename TEXT,
    company_name TEXT,
    distname TEXT,
    prod2 TEXT,
    comname TEXT,
    psu_pvt TEXT,
    statecode TEXT,
    orgrocd NUMERIC(18,4),
    dist TEXT,
    productname_org TEXT,
    total NUMERIC(18,4),
    distcode TEXT,
    comcode NUMERIC(18,4),
    code TEXT,
    productcode NUMERIC(18,4),
    zone_name TEXT,
    region_name TEXT,
    newcode TEXT,
    fiscal_year TEXT,
    coname TEXT,
    month_name TEXT,
    netweight_tmt NUMERIC(18,4),
    com_type TEXT,
    productname TEXT,
    prod1 TEXT,
    entity_id NUMERIC(18,4),
    updated_at DATE,
    created_at DATE,
    ro TEXT
);

CREATE INDEX IF NOT EXISTS idx_industry_performance_sbu ON industry_performance (sbu_name);


-- NL-SQL Pairs table for RAG few-shot
CREATE TABLE IF NOT EXISTS nl_sql_pairs (
    id SERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    sql_query TEXT NOT NULL,
    intent VARCHAR(50),
    question_embedding vector(768),
    embedding_model TEXT NOT NULL DEFAULT 'legacy',
    created_at TIMESTAMP DEFAULT NOW()
);

-- Schema Embeddings table
CREATE TABLE IF NOT EXISTS nlpsql_schema_embeddings (
    id SERIAL PRIMARY KEY,
    table_name TEXT NOT NULL,
    column_name TEXT,
    description TEXT NOT NULL,
    embedding vector(768),
    embedding_model TEXT NOT NULL DEFAULT 'legacy',
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Query History Table
CREATE TABLE IF NOT EXISTS nlpsql_query_history (
    id SERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    generated_sql TEXT NOT NULL,
    complexity VARCHAR(50),
    was_valid BOOLEAN,
    execution_time_ms INTEGER,
    row_count INTEGER,
    error_message TEXT,
    feedback INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_schema_embed ON nlpsql_schema_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_pairs_embed ON nl_sql_pairs USING hnsw (question_embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_schema_embedding_model ON nlpsql_schema_embeddings (embedding_model);
CREATE INDEX IF NOT EXISTS idx_pairs_embedding_model ON nl_sql_pairs (embedding_model);
