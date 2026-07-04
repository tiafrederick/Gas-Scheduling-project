-- ============================================================================
-- Natural Gas Scheduling Knowledge Engine — Canonical System of Record
-- Target dialect: DuckDB (portable to Postgres). See docs/canonical-model.md.
--
-- BITEMPORAL CONVENTION
--   valid_*  (a.k.a. business/valid time)  = when a fact is true in the real world
--                                            (contract term, gas-day window a capacity
--                                            setting applies to, rate effective window)
--   system_* (a.k.a. transaction time)     = when WE recorded/last-saw it from a posting
--   A row with system_to IS NULL is the currently-known version.
--   Notices are immutable events: they carry system_recorded_at only (append-only);
--   the *assertions* they make (capacity settings) are valid-timed in
--   capacity_impact_fact.
--
-- KEYS (DDL-002)
--   pipeline            : ferc_cid (FERC Company Identifier, e.g. 'C001203')
--   point               : composite (tsp_ferc_cid, loc) -> surrogate point_uid
--   interconnect        : edge resolved from a point's Up/Dn FERC CID + Up/Dn Loc
-- ============================================================================

-- ---------------------------------------------------------------------------
-- DIM: pipeline / transportation service provider (TSP)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pipeline (
    ferc_cid        VARCHAR PRIMARY KEY,     -- 'C001203'
    tsp_id          VARCHAR,                 -- numeric proprietary id, e.g. '808264746'
    name            VARCHAR NOT NULL,        -- 'Southeast Supply Header, LLC'
    short_code      VARCHAR,                 -- 'SESH' | 'CGT' | 'SABINE'
    ebb_platform    VARCHAR,                 -- 'TC eConnects' | 'Enbridge InfoPost' | 'gasnom'
    ebb_base_url    VARCHAR,
    is_portfolio    BOOLEAN DEFAULT FALSE,   -- TRUE = a pipe we actively schedule
    system_from     TIMESTAMP DEFAULT now(),
    system_to       TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- DIM: point (receipt / delivery / interconnect / storage location)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS point (
    point_uid       VARCHAR PRIMARY KEY,     -- tsp_ferc_cid || ':' || loc
    tsp_ferc_cid    VARCHAR NOT NULL REFERENCES pipeline(ferc_cid),
    loc             VARCHAR NOT NULL,        -- pipeline-scoped location code (e.g. '83004')
    loc_name        VARCHAR,
    loc_st_abbrev   VARCHAR,
    loc_cnty        VARCHAR,
    loc_zone        VARCHAR,
    loc_type_ind    VARCHAR,                 -- INT/END/STR/... (interconnect/endpoint/storage)
    dir_flo         VARCHAR,                 -- R receipt / D delivery / B bidirectional / N n-a
    loc_stat_ind    VARCHAR,                 -- A active
    eff_date        DATE,
    inact_date      DATE,
    source_file     VARCHAR,                 -- provenance
    system_from     TIMESTAMP DEFAULT now(),
    system_to       TIMESTAMP,
    UNIQUE (tsp_ferc_cid, loc, system_from)
);

-- ---------------------------------------------------------------------------
-- EDGE: interconnect (derived read-model source; DDL-001/010)
-- One row per point that declares a counterparty. b_* may be NULL/unresolved.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS interconnect (
    interconnect_uid       VARCHAR PRIMARY KEY,   -- deterministic hash of (a_point_uid, b keys)
    a_point_uid            VARCHAR NOT NULL REFERENCES point(point_uid),
    a_tsp_ferc_cid         VARCHAR NOT NULL,
    a_loc                  VARCHAR NOT NULL,
    b_point_uid            VARCHAR,                -- resolved counterparty point (nullable)
    b_tsp_ferc_cid         VARCHAR,                -- Up/Dn FERC CID as posted
    b_loc                  VARCHAR,                -- Up/Dn Loc as posted
    b_name_posted          VARCHAR,                -- Up/Dn (Loc) Name as posted (fuzzy fallback)
    updn_ind               VARCHAR,                -- Y/N interconnect indicator
    dir_flo                VARCHAR,                -- flow direction at a-side
    resolution_status      VARCHAR NOT NULL,       -- see docs/canonical-model.md tiers
    resolution_confidence  DOUBLE,                 -- 0..1
    source_file            VARCHAR,
    system_from            TIMESTAMP DEFAULT now(),
    system_to              TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- DIM/FACT: contract holdings (from Index of Customers postings)
-- valid time = contract term (term_start..term_end)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contract_holding (
    holding_uid     VARCHAR PRIMARY KEY,
    tsp_ferc_cid    VARCHAR NOT NULL REFERENCES pipeline(ferc_cid),
    holder_name     VARCHAR NOT NULL,        -- 'BP ENERGY COMPANY'
    holder_id       VARCHAR,                 -- shipper id, e.g. '625275755'
    rate_schedule   VARCHAR,                 -- 'FTS' | 'IT' | ...
    contract_id     VARCHAR,                 -- K number, e.g. '840245-R1'
    neg_rate_ind    VARCHAR,                 -- Y/N negotiated rate
    mdq_dth         BIGINT,                  -- maximum daily quantity (Dth/d)
    term_start      DATE,                    -- valid time start
    term_end        DATE,                    -- valid time end
    source_file     VARCHAR,
    system_from     TIMESTAMP DEFAULT now(),
    system_to       TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contract_point (
    holding_uid     VARCHAR NOT NULL REFERENCES contract_holding(holding_uid),
    point_uid       VARCHAR,                 -- resolved point (nullable if unresolved)
    loc             VARCHAR,
    loc_name        VARCHAR,
    rec_del         VARCHAR,                 -- posting role code (e.g. 'MQ')
    qty_dth         BIGINT
);

-- ---------------------------------------------------------------------------
-- EVENT: notice (immutable, append-only). Lifecycle via notice_stat_desc +
-- supersession via prior_notice_id (DDL-003).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notice (
    notice_uid          VARCHAR PRIMARY KEY,     -- tsp_ferc_cid || ':' || notice_id
    tsp_ferc_cid        VARCHAR NOT NULL REFERENCES pipeline(ferc_cid),
    notice_id           VARCHAR NOT NULL,
    notice_type         VARCHAR,                 -- Maintenance/Capacity Constraint/Force Majeure/Other
    notice_stat_desc    VARCHAR,                 -- Initiate/Update/Complete
    critical            BOOLEAN,
    reqrd_rsp_desc      VARCHAR,
    subject             VARCHAR,
    author              VARCHAR,
    body_text           TEXT,
    post_dt             TIMESTAMP,
    effective_dt        TIMESTAMP,
    end_dt              TIMESTAMP,
    response_dt         TIMESTAMP,
    prior_notice_id     VARCHAR,                 -- supersession link (self-ref by notice_id)
    source_file         VARCHAR,
    system_recorded_at  TIMESTAMP DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- FACT: capacity impact extracted from a notice body (DDL-003).
-- valid time = gas-day window + cycle the setting applies to.
-- Every row carries provenance (source_span), method, model, confidence, and a
-- human verification slot. NEVER trust an unverified low-confidence Dth value.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS capacity_impact_fact (
    fact_uid            VARCHAR PRIMARY KEY,
    notice_uid          VARCHAR NOT NULL REFERENCES notice(notice_uid),
    tsp_ferc_cid        VARCHAR NOT NULL,
    asset_name          VARCHAR,                 -- 'AlexSEG'
    asset_kind          VARCHAR,                 -- compressor_segment/station/lateral/point
    related_point_uids  VARCHAR[],               -- resolved points, if any
    direction           VARCHAR,                 -- backhaul/forwardhaul/both
    metric              VARCHAR,                 -- estimated_capacity_setting/design_capacity/posted_percentage
    value_low           DOUBLE,
    value_high          DOUBLE,                  -- = value_low when a single value
    uom                 VARCHAR,                 -- Dth/percent
    valid_gas_day_from  DATE,
    valid_gas_day_to    DATE,
    effective_cycle     VARCHAR,                 -- TIMELY/EVENING/ID1/ID2/ID3
    affects_services    VARCHAR[],               -- ['Interruptible','Secondary Firm','Primary Firm']
    source_span         TEXT,                    -- exact substring the value came from
    extraction_method   VARCHAR,                 -- llm/regex/manual
    extraction_model    VARCHAR,                 -- e.g. 'claude-fable-5'
    confidence          DOUBLE,                  -- 0..1
    system_recorded_at  TIMESTAMP DEFAULT now(),
    verified_by         VARCHAR,                 -- human sign-off (NULL until reviewed)
    verified_at         TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- FACT: rate (from tariff / notice of rate change). valid time = effective window.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rate_fact (
    rate_uid            VARCHAR PRIMARY KEY,
    tsp_ferc_cid        VARCHAR NOT NULL REFERENCES pipeline(ferc_cid),
    rate_schedule       VARCHAR,                 -- FT-1/IT-1/...
    component           VARCHAR,                 -- reservation_monthly/reservation_daily/usage/FRP/UFRP
    rec_section         VARCHAR,                 -- pressure section, e.g. 'HH' (for FRP matrix)
    del_section         VARCHAR,
    value               DOUBLE,
    uom                 VARCHAR,                 -- $/Dt or percent
    ferc_docket         VARCHAR,                 -- 'RP26-898-000'
    valid_from          DATE,
    valid_to            DATE,
    source_file         VARCHAR,
    system_recorded_at  TIMESTAMP DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- FACT: point-level operational capacity (from OAC / daily capacity screens).
-- valid time = (gas_day, cycle). Placeholder shape; refine when we parse the
-- OAC/OA_MLC tables (formats NOT yet assumed — see risk #7).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS operational_capacity_fact (
    cap_uid             VARCHAR PRIMARY KEY,
    point_uid           VARCHAR REFERENCES point(point_uid),
    tsp_ferc_cid        VARCHAR NOT NULL,
    gas_day             DATE NOT NULL,
    cycle               VARCHAR NOT NULL,        -- TIMELY/EVENING/ID1/ID2/ID3
    design_capacity     DOUBLE,
    operating_capacity  DOUBLE,
    total_scheduled     DOUBLE,
    oper_avail_capacity DOUBLE,
    source_file         VARCHAR,
    system_recorded_at  TIMESTAMP DEFAULT now()
);
