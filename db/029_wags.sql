-- 029_wags.sql — the context layer and Wags (Chat 2, Phase 4).
--
-- THE CONTEXT LAYER IS SQL. Wags (the web, as the owner, RLS applied) and the
-- Morning Brief (the worker, Phase 5) read ONE implementation, always fresh:
--
--   context_providers       the registry: one row per pillar, naming a
--                           provider function. Only migrations write it.
--   ctx_goals()             each provider: SECURITY INVOKER, STABLE, returns
--   ctx_ventures()          compact jsonb (long text trimmed, counts over
--   ctx_capital()           lists; aimed under ~800 tokens each).
--   context_snapshot()      every enabled provider in order, plus built_at,
--                           the owner's local date/time and ISO week. A
--                           provider that raises is reported as
--                           {"error": ...} under its key and never sinks the
--                           whole snapshot.
--   context_snapshot_record()  builds a snapshot and stores it in
--                           context_snapshots, deduped by a hash of the
--                           payload without its clock fields, returning the
--                           row id. Called when Wags uses a snapshot, so what
--                           Wags knew is always on record.
--
-- WAGS:
--   wags_threads    owner's threads. Title and archived_at are editable;
--                   deleting a thread deletes its messages (cascade).
--   wags_messages   append-only. role 'user' | 'assistant' | 'tool'. A 'tool'
--                   row is the outcome of a proposal (confirmed/dismissed),
--                   written as its own row because the assistant message
--                   that proposed it can never be edited. The only delete is
--                   the cascade from deleting the thread (venture_log's rule).
--   ai_budget_state()  month-to-date spend and whether the 80%/100% alerts
--                   went out this month, from ai_usage and notifications:
--                   the same rows and the same month boundary the worker's
--                   core/ai.py guard uses, so neither side double-sends.
--
-- Access (db/019 onward): RLS on, owner policies TO authenticated, nothing
-- for anon. valemont_readonly (db/018, db/028) gets EXECUTE on the new
-- SECURITY INVOKER STABLE functions only; never on context_snapshot_record
-- (VOLATILE: it writes).
--
-- Prerequisites: db/019, db/020, db/021, db/022, db/026, db/027, db/028.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- Providers.

-- Text trimmed to n characters, with an ellipsis when cut.
CREATE OR REPLACE FUNCTION public.ctx_trim(p_text text, p_max integer)
RETURNS text
LANGUAGE sql
IMMUTABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT CASE
        WHEN p_text IS NULL THEN NULL
        WHEN char_length(p_text) <= p_max THEN p_text
        ELSE left(p_text, p_max - 1) || '…'
    END;
$$;

-- Goals: this week (open / done / moved, with carry counts and the linked
-- venture), this month, long term, and the last four weeks' completion, by
-- the slot rule (src/lib/goals/types.ts holdsSlot: open or done, not moved).
CREATE OR REPLACE FUNCTION public.ctx_goals()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH today AS (
        SELECT public.goal_local_today() AS d
    ),
    periods AS (
        SELECT date_trunc('week', d)::date AS week, date_trunc('month', d)::date AS month FROM today
    ),
    g AS (
        SELECT g.*,
               EXISTS (SELECT 1 FROM public.goals c WHERE c.carried_from = g.id) AS moved,
               v.slug AS venture_slug, v.name AS venture_name
          FROM public.goals g
          LEFT JOIN public.ventures v ON v.id = g.venture_id
    ),
    week AS (
        SELECT g.* FROM g, periods p WHERE g.horizon = 'weekly' AND g.period_start = p.week
    ),
    history AS (
        SELECT g.period_start,
               count(*) FILTER (WHERE NOT g.moved AND g.status = 'done') AS done,
               count(*) FILTER (WHERE NOT g.moved AND g.status IN ('open', 'done')) AS held
          FROM g, periods p
         WHERE g.horizon = 'weekly' AND g.period_start < p.week AND g.period_start >= p.week - 28
         GROUP BY g.period_start
    )
    SELECT jsonb_build_object(
        'week_of', (SELECT week FROM periods),
        'week', jsonb_build_object(
            'done', (SELECT count(*) FROM week WHERE NOT moved AND status = 'done'),
            'held', (SELECT count(*) FROM week WHERE NOT moved AND status IN ('open', 'done')),
            'cap', 10,
            'goals', coalesce((
                SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                           'title', public.ctx_trim(w.title, 90),
                           'state', CASE WHEN w.moved THEN 'moved' ELSE w.status END,
                           'area', w.area,
                           'carried', NULLIF(w.carry_count, 0),
                           'venture', w.venture_slug))
                         ORDER BY CASE WHEN w.moved THEN 3 WHEN w.status = 'open' THEN 0
                                       WHEN w.status = 'done' THEN 1 ELSE 2 END, w.sort_order, w.created_at)
                  FROM (SELECT * FROM week ORDER BY moved, status, sort_order LIMIT 15) w), '[]'::jsonb),
            'more', GREATEST((SELECT count(*) FROM week) - 15, 0)),
        'month', coalesce((
            SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                       'title', public.ctx_trim(m.title, 90),
                       'state', CASE WHEN m.moved THEN 'moved' ELSE m.status END,
                       'carried', NULLIF(m.carry_count, 0),
                       'venture', m.venture_slug)) ORDER BY m.sort_order, m.created_at)
              FROM (SELECT g.* FROM g, periods p
                     WHERE g.horizon = 'monthly' AND g.period_start = p.month
                     ORDER BY g.sort_order, g.created_at LIMIT 10) m), '[]'::jsonb),
        'long_term', coalesce((
            SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                       'title', public.ctx_trim(l.title, 90),
                       'state', l.status,
                       'venture', l.venture_slug)) ORDER BY l.sort_order, l.created_at)
              FROM (SELECT g.* FROM g WHERE g.horizon = 'long_term' AND g.status = 'open'
                     ORDER BY g.sort_order, g.created_at LIMIT 10) l), '[]'::jsonb),
        'last_4_weeks', coalesce((
            SELECT jsonb_agg(jsonb_build_object('week_of', h.period_start, 'done', h.done, 'held', h.held)
                             ORDER BY h.period_start DESC)
              FROM history h), '[]'::jsonb)
    );
$$;

-- Ventures: the set-up ones (src/lib/ventures/types.ts isSetUp: any of stage,
-- next action, tagline, role) with stage, next action, blockers, active
-- workstreams, open dates (overdue flagged) and the last 3 log entries; then
-- the names of those not set up, and of the archived.
CREATE OR REPLACE FUNCTION public.ctx_ventures()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH today AS (SELECT public.goal_local_today() AS d),
    live AS (
        SELECT v.* FROM public.ventures v WHERE v.archived_at IS NULL
    ),
    setup AS (
        SELECT * FROM live
         WHERE stage IS NOT NULL OR next_action IS NOT NULL OR tagline IS NOT NULL OR role IS NOT NULL
    )
    SELECT jsonb_build_object(
        'active', coalesce((
            SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                'name', s.name,
                'slug', s.slug,
                'stage', s.stage,
                'role', public.ctx_trim(s.role, 60),
                'next_action', public.ctx_trim(s.next_action, 160),
                'blockers', public.ctx_trim(s.blockers, 200),
                'workstreams', (
                    SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                               'name', w.name,
                               'next_action', public.ctx_trim(w.next_action, 120)))
                             ORDER BY w.sort_order, w.created_at)
                      FROM (SELECT * FROM public.venture_workstreams w
                             WHERE w.venture_id = s.id AND w.state = 'active'
                             ORDER BY w.sort_order, w.created_at LIMIT 6) w),
                'dates', (
                    SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                               'label', public.ctx_trim(d.label, 80),
                               'due', d.due_on,
                               'overdue', CASE WHEN d.due_on < t.d THEN true END))
                             ORDER BY d.due_on)
                      FROM (SELECT * FROM public.venture_dates d
                             WHERE d.venture_id = s.id AND d.done_at IS NULL
                             ORDER BY d.due_on LIMIT 5) d, today t),
                'log', (
                    SELECT jsonb_agg(jsonb_build_object(
                               'on', (l.created_at AT TIME ZONE coalesce(
                                         (SELECT timezone FROM public.app_settings LIMIT 1), 'America/Chicago'))::date,
                               'kind', l.kind,
                               'entry', public.ctx_trim(l.entry, 110))
                             ORDER BY l.id DESC)
                      FROM (SELECT * FROM public.venture_log l
                             WHERE l.venture_id = s.id ORDER BY l.id DESC LIMIT 3) l)
            )) ORDER BY s.sort_order, s.name)
              FROM setup s), '[]'::jsonb),
        'not_set_up', coalesce((
            SELECT jsonb_agg(l.name ORDER BY l.sort_order, l.name)
              FROM live l WHERE l.id NOT IN (SELECT id FROM setup)), '[]'::jsonb),
        'archived', coalesce((
            SELECT jsonb_agg(v.name ORDER BY v.name)
              FROM public.ventures v WHERE v.archived_at IS NOT NULL), '[]'::jsonb)
    );
$$;

-- Capital: per mode, never summed across modes. Total and today's change
-- (v_capital_today), the last 7 snapshot days, the last 5 real entries
-- (test entries excluded, db/027). Paper is always labelled paper.
CREATE OR REPLACE FUNCTION public.ctx_capital()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT jsonb_build_object(
        'note', 'Paper and live are separate records. Never add them together.',
        'modes', coalesce((
            SELECT jsonb_object_agg(t.mode, jsonb_strip_nulls(jsonb_build_object(
                'label', CASE t.mode WHEN 'paper' THEN 'PAPER (simulated money)' ELSE 'LIVE (real money)' END,
                'total', t.value,
                'change_today', t.change,
                'prior_snapshot', t.prior_date,
                'last_7_days', (
                    SELECT jsonb_agg(jsonb_build_object('day', s.snap_date, 'value', s.value) ORDER BY s.snap_date)
                      FROM (SELECT s.snap_date, sum(s.value) AS value
                              FROM public.capital_snapshots s
                             WHERE s.mode = t.mode
                             GROUP BY s.snap_date
                             ORDER BY s.snap_date DESC LIMIT 7) s),
                'last_entries', (
                    SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                               'on', (e.created_at AT TIME ZONE coalesce(
                                         (SELECT timezone FROM public.app_settings LIMIT 1), 'America/Chicago'))::date,
                               'kind', e.kind,
                               'amount', CASE e.kind WHEN 'withdrawal' THEN -e.amount ELSE e.amount END,
                               'note', public.ctx_trim(e.note, 80)))
                             ORDER BY e.id DESC)
                      FROM (SELECT * FROM public.bankroll_entries e
                             WHERE e.mode = t.mode AND NOT e.is_test
                             ORDER BY e.id DESC LIMIT 5) e)
            )))
              FROM public.v_capital_today t
             WHERE t.is_total), '{}'::jsonb)
    );
$$;

-- ===================================================================== 2
-- The registry.

CREATE TABLE IF NOT EXISTS context_providers (
    key            TEXT          PRIMARY KEY
                                 CONSTRAINT context_providers_key_format CHECK (key ~ '^[a-z][a-z0-9_]*$'),
    label          TEXT          NOT NULL CHECK (char_length(label) BETWEEN 1 AND 60),
    fn             REGPROCEDURE  NOT NULL,
    brief_section  BOOLEAN       NOT NULL DEFAULT true,
    sort           INTEGER       NOT NULL DEFAULT 0,
    enabled        BOOLEAN       NOT NULL DEFAULT true
);

INSERT INTO context_providers (key, label, fn, brief_section, sort) VALUES
    ('goals',    'Goals',    'public.ctx_goals()'::regprocedure,    true, 10),
    ('ventures', 'Ventures', 'public.ctx_ventures()'::regprocedure, true, 20),
    ('capital',  'Capital',  'public.ctx_capital()'::regprocedure,  true, 30)
ON CONFLICT (key) DO NOTHING;

-- Every enabled provider in order, each isolated: one that raises becomes
-- {"error": "<message>"} under its key. The clock fields come last in the
-- object so a reader sees the data first; context_snapshot_record() hashes
-- the payload without them.
CREATE OR REPLACE FUNCTION public.context_snapshot()
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
    p        record;
    v_out    jsonb := '{}'::jsonb;
    v_part   jsonb;
    v_tz     text := coalesce((SELECT timezone FROM public.app_settings LIMIT 1), 'America/Chicago');
    v_local  timestamp := now() AT TIME ZONE v_tz;
BEGIN
    FOR p IN SELECT c.key, c.fn FROM public.context_providers c WHERE c.enabled ORDER BY c.sort, c.key LOOP
        BEGIN
            EXECUTE 'SELECT ' || p.fn::text INTO v_part;
            v_out := v_out || jsonb_build_object(p.key, coalesce(v_part, 'null'::jsonb));
        EXCEPTION WHEN OTHERS THEN
            v_out := v_out || jsonb_build_object(p.key, jsonb_build_object('error', left(SQLERRM, 300)));
        END;
    END LOOP;
    RETURN v_out || jsonb_build_object(
        'built_at', now(),
        'local_date', v_local::date,
        'local_time', to_char(v_local, 'HH24:MI'),
        'weekday', trim(to_char(v_local, 'Day')),
        'iso_week', to_char(v_local, 'IYYY-"W"IW'),
        'timezone', v_tz
    );
END;
$$;

-- ===================================================================== 3
-- What Wags knew.

CREATE TABLE IF NOT EXISTS context_snapshots (
    id        BIGSERIAL    PRIMARY KEY,
    owner_id  UUID         NOT NULL DEFAULT auth.uid()
                           REFERENCES auth.users (id) ON DELETE RESTRICT,
    built_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    hash      TEXT         NOT NULL CHECK (hash ~ '^[0-9a-f]{64}$'),
    payload   JSONB        NOT NULL,
    CONSTRAINT context_snapshots_owner_hash_key UNIQUE (owner_id, hash)
);

DROP TRIGGER IF EXISTS context_snapshots_immutable ON context_snapshots;
CREATE TRIGGER context_snapshots_immutable
    BEFORE UPDATE OR DELETE ON context_snapshots
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS context_snapshots_no_truncate ON context_snapshots;
CREATE TRIGGER context_snapshots_no_truncate
    BEFORE TRUNCATE ON context_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- The hash leaves out the clock fields, so an unchanged pillar state maps to
-- one row however often Wags is asked. Returns {"id", "payload"}; the payload
-- is the fresh one (current clock), the row keeps the first.
CREATE OR REPLACE FUNCTION public.context_snapshot_record()
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
    -- The owner's session on the web; the worker (Phase 5, no session) files
    -- under the owner in app_settings.
    v_owner   uuid  := coalesce(auth.uid(), (SELECT a.owner_id FROM public.app_settings a LIMIT 1));
    v_payload jsonb := public.context_snapshot();
    v_hash    text  := encode(sha256(convert_to(
        (v_payload - 'built_at' - 'local_time' - 'weekday' - 'local_date')::text, 'UTF8')), 'hex');
    v_id      bigint;
BEGIN
    INSERT INTO public.context_snapshots (owner_id, hash, payload)
    VALUES (v_owner, v_hash, v_payload)
    ON CONFLICT (owner_id, hash) DO NOTHING
    RETURNING id INTO v_id;
    IF v_id IS NULL THEN
        SELECT s.id INTO v_id FROM public.context_snapshots s
         WHERE s.owner_id = v_owner AND s.hash = v_hash;
    END IF;
    RETURN jsonb_build_object('id', v_id, 'payload', v_payload);
END;
$$;

-- ===================================================================== 4
-- Threads and messages.

CREATE TABLE IF NOT EXISTS wags_threads (
    id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id     UUID         NOT NULL DEFAULT auth.uid()
                              REFERENCES auth.users (id) ON DELETE RESTRICT,
    title        TEXT         CONSTRAINT wags_threads_title_length CHECK (char_length(title) BETWEEN 1 AND 120),
    origin_page  TEXT         CONSTRAINT wags_threads_origin_page CHECK (
                                  origin_page IS NULL
                                  OR (origin_page LIKE '/%' AND origin_page NOT LIKE '//%'
                                      AND char_length(origin_page) <= 200)),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    archived_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS wags_threads_owner_updated ON wags_threads (owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS wags_messages (
    id                   BIGSERIAL      PRIMARY KEY,
    thread_id            UUID           NOT NULL REFERENCES wags_threads (id) ON DELETE CASCADE,
    owner_id             UUID           NOT NULL DEFAULT auth.uid()
                                        REFERENCES auth.users (id) ON DELETE RESTRICT,
    role                 TEXT           NOT NULL
                                        CONSTRAINT wags_messages_role_check CHECK (role IN ('user', 'assistant', 'tool')),
    content              TEXT           NOT NULL DEFAULT '',
    -- The AI SDK UI parts (text, tool calls, tool outputs), as streamed.
    parts                JSONB          NOT NULL DEFAULT '[]'::jsonb
                                        CONSTRAINT wags_messages_parts_array CHECK (jsonb_typeof(parts) = 'array'),
    context_snapshot_id  BIGINT         REFERENCES context_snapshots (id) ON DELETE RESTRICT,
    page_context         JSONB,
    model                TEXT,
    persona_version      TEXT,
    tokens_in            INTEGER        CHECK (tokens_in >= 0),
    tokens_out           INTEGER        CHECK (tokens_out >= 0),
    cache_read           INTEGER        CHECK (cache_read >= 0),
    cache_write          INTEGER        CHECK (cache_write >= 0),
    cost_usd             NUMERIC(12,6)  CHECK (cost_usd >= 0),
    ai_usage_id          BIGINT         REFERENCES ai_usage (id) ON DELETE RESTRICT,
    -- A stream that died part-way: what arrived is kept, and marked.
    incomplete           BOOLEAN        NOT NULL DEFAULT false,
    created_at           TIMESTAMPTZ    NOT NULL DEFAULT now(),
    -- The composer's limit, held here too so no client can skip it.
    CONSTRAINT wags_messages_user_length CHECK (role <> 'user' OR char_length(content) BETWEEN 1 AND 4000)
);

CREATE INDEX IF NOT EXISTS wags_messages_thread ON wags_messages (thread_id, id);
CREATE INDEX IF NOT EXISTS wags_messages_owner_role_time ON wags_messages (owner_id, role, created_at);

CREATE OR REPLACE FUNCTION public.wags_messages_append_only()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    -- The one delete allowed: the cascade from deleting the thread. By the
    -- time the cascade reaches this row the thread is gone.
    IF TG_OP = 'DELETE' AND NOT EXISTS (SELECT 1 FROM public.wags_threads t WHERE t.id = OLD.thread_id) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'wags_messages is append-only: % refused (write a new message instead)', TG_OP;
END;
$$;

DROP TRIGGER IF EXISTS wags_messages_immutable ON wags_messages;
CREATE TRIGGER wags_messages_immutable
    BEFORE UPDATE OR DELETE ON wags_messages
    FOR EACH ROW EXECUTE FUNCTION public.wags_messages_append_only();
DROP TRIGGER IF EXISTS wags_messages_no_truncate ON wags_messages;
CREATE TRIGGER wags_messages_no_truncate
    BEFORE TRUNCATE ON wags_messages
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- A new message moves its thread to the top of the list.
CREATE OR REPLACE FUNCTION public.wags_threads_touch()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    UPDATE public.wags_threads SET updated_at = now() WHERE id = NEW.thread_id;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS wags_messages_touch_thread ON wags_messages;
CREATE TRIGGER wags_messages_touch_thread
    AFTER INSERT ON wags_messages
    FOR EACH ROW EXECUTE FUNCTION public.wags_threads_touch();

-- Wags's rate limit, counted by the database's clock: the owner's user
-- messages in the last p_seconds (RLS scopes it to the caller).
CREATE OR REPLACE FUNCTION public.wags_user_messages_since(p_seconds integer)
RETURNS integer
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT count(*)::integer
      FROM public.wags_messages m
     WHERE m.role = 'user'
       AND m.created_at > now() - make_interval(secs => greatest(p_seconds, 0));
$$;

-- ===================================================================== 5
-- The AI budget's shared state (core/ai.py guard; apps/web/src/lib/ai/budget.ts).

CREATE OR REPLACE FUNCTION public.ai_budget_state()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH m AS (
        SELECT (date_trunc('month', now() AT TIME ZONE coalesce(
                    (SELECT timezone FROM public.app_settings LIMIT 1), 'America/Chicago'))
                AT TIME ZONE coalesce((SELECT timezone FROM public.app_settings LIMIT 1), 'America/Chicago'))
               AS start
    )
    SELECT jsonb_build_object(
        'month_start', m.start,
        'spent_usd', (SELECT coalesce(sum(u.cost_usd), 0) FROM public.ai_usage u WHERE u.created_at >= m.start),
        'alerted_80', EXISTS (SELECT 1 FROM public.notifications n
                               WHERE n.kind = 'ai_budget_80' AND n.status <> 'queued' AND n.created_at >= m.start),
        'alerted_100', EXISTS (SELECT 1 FROM public.notifications n
                                WHERE n.kind = 'ai_budget_100' AND n.status <> 'queued' AND n.created_at >= m.start)
    )
    FROM m;
$$;

-- ===================================================================== 6
-- Access.

ALTER TABLE context_providers  ENABLE ROW LEVEL SECURITY;
ALTER TABLE context_snapshots  ENABLE ROW LEVEL SECURITY;
ALTER TABLE wags_threads       ENABLE ROW LEVEL SECURITY;
ALTER TABLE wags_messages      ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON context_providers, context_snapshots, wags_threads, wags_messages FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE context_snapshots_id_seq, wags_messages_id_seq FROM anon, authenticated, PUBLIC;

-- The registry: read-only to the owner. Only migrations add a provider, so
-- nothing at runtime can point context_snapshot() at another function.
GRANT SELECT ON context_providers TO authenticated;
DROP POLICY IF EXISTS context_providers_owner_read ON context_providers;
CREATE POLICY context_providers_owner_read ON context_providers
    FOR SELECT TO authenticated USING ((SELECT public.is_owner()));

GRANT SELECT, INSERT ON context_snapshots TO authenticated;
GRANT USAGE ON SEQUENCE context_snapshots_id_seq TO authenticated;
DROP POLICY IF EXISTS context_snapshots_owner_read ON context_snapshots;
CREATE POLICY context_snapshots_owner_read ON context_snapshots
    FOR SELECT TO authenticated USING (owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS context_snapshots_owner_append ON context_snapshots;
CREATE POLICY context_snapshots_owner_append ON context_snapshots
    FOR INSERT TO authenticated WITH CHECK (owner_id = (SELECT auth.uid()));

-- Threads: the owner creates, renames, archives and deletes. Only title,
-- archived_at and updated_at can change.
GRANT SELECT, INSERT, DELETE ON wags_threads TO authenticated;
GRANT UPDATE (title, archived_at, updated_at) ON wags_threads TO authenticated;
DROP POLICY IF EXISTS wags_threads_owner ON wags_threads;
CREATE POLICY wags_threads_owner ON wags_threads
    FOR ALL TO authenticated
    USING (owner_id = (SELECT auth.uid()))
    WITH CHECK (owner_id = (SELECT auth.uid()));

-- Messages: read and append, into the owner's own threads only.
GRANT SELECT, INSERT ON wags_messages TO authenticated;
GRANT USAGE ON SEQUENCE wags_messages_id_seq TO authenticated;
DROP POLICY IF EXISTS wags_messages_owner_read ON wags_messages;
CREATE POLICY wags_messages_owner_read ON wags_messages
    FOR SELECT TO authenticated USING (owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS wags_messages_owner_append ON wags_messages;
CREATE POLICY wags_messages_owner_append ON wags_messages
    FOR INSERT TO authenticated
    WITH CHECK (owner_id = (SELECT auth.uid())
                AND EXISTS (SELECT 1 FROM public.wags_threads t WHERE t.id = thread_id));
-- No DELETE grant: a thread delete's cascade runs as the table owner (as
-- venture_log's does), and the trigger above lets only that through.

-- Functions.
REVOKE ALL ON FUNCTION public.wags_messages_append_only() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.wags_threads_touch() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION
    public.ctx_trim(text, integer),
    public.ctx_goals(),
    public.ctx_ventures(),
    public.ctx_capital(),
    public.context_snapshot(),
    public.context_snapshot_record(),
    public.ai_budget_state(),
    public.wags_user_messages_since(integer)
FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION
    public.ctx_trim(text, integer),
    public.ctx_goals(),
    public.ctx_ventures(),
    public.ctx_capital(),
    public.context_snapshot(),
    public.context_snapshot_record(),
    public.ai_budget_state(),
    public.wags_user_messages_since(integer)
TO authenticated;
-- db/028's rule: the read-only role executes the readers, never a writer.
GRANT EXECUTE ON FUNCTION
    public.ctx_trim(text, integer),
    public.ctx_goals(),
    public.ctx_ventures(),
    public.ctx_capital(),
    public.context_snapshot(),
    public.ai_budget_state(),
    public.wags_user_messages_since(integer)
TO valemont_readonly;

INSERT INTO migration_log (name) VALUES ('029_wags')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) three providers: goals, ventures, capital, in that order, enabled
--   (b) the snapshot's top-level keys: built_at, capital, goals, iso_week,
--       local_date, local_time, timezone, ventures, weekday; and no "error"
--       under any provider
--   (c) RLS on for all four tables; anon has no privilege on any
--   (d) readonly_can: true for the seven readers, false for
--       context_snapshot_record
--   (e) the migration_log row
-- ---------------------------------------------------------------------------

SELECT key, fn::text, brief_section, sort, enabled FROM context_providers ORDER BY sort;  -- (a)

SELECT jsonb_object_keys(context_snapshot()) AS key ORDER BY 1;                          -- (b)
SELECT k, context_snapshot() -> k ? 'error' AS has_error
  FROM unnest(ARRAY['goals', 'ventures', 'capital']) AS k;                                -- (b)

SELECT c.relname, c.relrowsecurity,
       has_table_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,DELETE') AS anon_any
  FROM pg_class c
 WHERE c.relname IN ('context_providers', 'context_snapshots', 'wags_threads', 'wags_messages')
 ORDER BY 1;                                                                              -- (c)

SELECT p.proname, p.provolatile::text AS volatility,
       has_function_privilege('valemont_readonly', p.oid, 'EXECUTE') AS readonly_can
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'public'
   AND p.proname IN ('ctx_trim', 'ctx_goals', 'ctx_ventures', 'ctx_capital',
                     'context_snapshot', 'context_snapshot_record', 'ai_budget_state',
                     'wags_user_messages_since')
 ORDER BY readonly_can DESC, 1;                                                           -- (d)

SELECT name, applied_at FROM migration_log WHERE name = '029_wags';                     -- (e)
