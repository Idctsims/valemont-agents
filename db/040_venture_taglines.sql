-- 040_venture_taglines.sql — each venture's one-line "what it does" as its
-- Ventures HQ tagline, and ctx_ventures() carries the tagline (Chat 3,
-- Phase 2; owner, 2026-10-12).
--
-- WHY. The feed's "Why it matters" job (workers/feed/why.py) sends Haiku the
-- venture context from ctx_ventures(). That context had stage, role and next
-- action, but no tagline, and for a venture that is not set up only its
-- NAME: Haiku could not tell what six of the seven ventures do. The owner's
-- one-liners become the taglines (the owner edits them in Ventures HQ from
-- here on, through the existing server action), and ctx_ventures() includes
-- the tagline, so Wags and the Morning Brief see it too.
--
-- Setting a tagline marks a venture "set up" (src/lib/ventures/types.ts
-- isSetUp; ctx_ventures' 'active' list): six ventures move from "not set up"
-- to set up. The owner accepted that (2026-10-12). Taglines do not write a
-- venture_log entry (db/022: only stage, next_action and blockers do).
--
-- SAFE TO RE-RUN, and never clobbers a later edit: each tagline is set only
-- when it is still NULL, already the new text, or (Sail Beach Club) the seed
-- text "Experiential hospitality · Biscayne Bay". Any other value is left as
-- it is and reported in a NOTICE.
--
-- A self-test runs on paste (rolled back): ctx_ventures() returns each live
-- venture's tagline in 'active', the same keys as before, and no archived
-- venture. A wrong result raises and nothing in this file is applied.
--
-- Prerequisites: db/022 (ventures), db/029 (ctx_ventures, ctx_trim).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- The taglines.

DO $$
DECLARE
    r        record;
    v_cur    text;
    v_set    int := 0;
    v_kept   int := 0;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        ('sail-beach-club',           'Experiential hospitality: a beach/members'' club on Biscayne Bay, Miami.',
                                      'Experiential hospitality · Biscayne Bay'),
        ('perfect-timing-management', 'Artist and influencer management.', NULL),
        ('excursion',                 'A media collective on the business side of cultural figures.', NULL),
        ('clipd',                     'SaaS.', NULL),
        ('valemont-grow',             'AI marketing automation.', NULL),
        ('sims-vale-capital',         'Family office.', NULL),
        ('freelance-web-dev',         'Next.js/Supabase sites (senior care vertical).', NULL)
    ) AS t (slug, tagline, seed)
    LOOP
        SELECT v.tagline INTO v_cur FROM public.ventures v WHERE v.slug = r.slug;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'db/040: venture % not found. Nothing was applied.', r.slug;
        END IF;
        IF v_cur IS NULL OR v_cur = r.tagline OR v_cur = r.seed THEN
            UPDATE public.ventures SET tagline = r.tagline WHERE slug = r.slug AND tagline IS DISTINCT FROM r.tagline;
            v_set := v_set + 1;
        ELSE
            RAISE NOTICE 'db/040: % keeps its tagline %, edited after the seed', r.slug, v_cur;
            v_kept := v_kept + 1;
        END IF;
    END LOOP;
    RAISE NOTICE 'db/040: % taglines set (or already set), % kept as edited', v_set, v_kept;
END
$$;

-- ===================================================================== 2
-- ctx_ventures(), as db/029 wrote it, plus 'tagline' on each set-up venture.

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
                'tagline', public.ctx_trim(s.tagline, 200),
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

-- db/029's grants on ctx_ventures() survive CREATE OR REPLACE.

-- ===================================================================== 3
-- Self-test, rolled back.

DO $$
DECLARE
    v_ctx   jsonb;
    v_owner uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_id    uuid;
BEGIN
    BEGIN
        INSERT INTO public.ventures (owner_id, name, slug, tagline, sort_order)
        VALUES (v_owner, 'Self-test 040', 'selftest-040', 'A self-test tagline.', 999)
        RETURNING id INTO v_id;
        v_ctx := public.ctx_ventures();

        -- 1. The tagline rides on the set-up venture.
        IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_ctx -> 'active') a
                        WHERE a ->> 'slug' = 'selftest-040' AND a ->> 'tagline' = 'A self-test tagline.') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_ventures has no tagline for a venture that has one: %', left(v_ctx::text, 300);
        END IF;
        -- 2. The same three keys as before.
        IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(v_ctx) k) IS DISTINCT FROM ARRAY['active', 'archived', 'not_set_up'] THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_ventures keys changed';
        END IF;
        -- 3. An archived venture is never active.
        UPDATE public.ventures SET archived_at = now() WHERE id = v_id;
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(public.ctx_ventures() -> 'active') a
                    WHERE a ->> 'slug' = 'selftest-040') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an archived venture is in active';
        END IF;
        -- 4. The real taglines landed (when no later edit kept another one).
        IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(public.ctx_ventures() -> 'active') a
                        WHERE a ->> 'slug' = 'perfect-timing-management'
                          AND a ->> 'tagline' = 'Artist and influencer management.')
           AND (SELECT tagline FROM public.ventures WHERE slug = 'perfect-timing-management') IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: Perfect Timing Management has no tagline in ctx_ventures';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '040 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('040_venture_taglines')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) seven live ventures, each with the tagline above
--   (b) ctx_ventures: 7 active, each with a tagline; not_set_up empty
--   (c) the migration_log row (and the NOTICE "040 self-test passed")
-- ---------------------------------------------------------------------------

SELECT name, tagline FROM ventures WHERE archived_at IS NULL ORDER BY sort_order;          -- (a)

SELECT jsonb_array_length(ctx_ventures() -> 'active') AS active,
       (SELECT count(*) FROM jsonb_array_elements(ctx_ventures() -> 'active') a WHERE a ? 'tagline') AS with_tagline,
       jsonb_array_length(ctx_ventures() -> 'not_set_up') AS not_set_up;                 -- (b)

SELECT name, applied_at FROM migration_log WHERE name = '040_venture_taglines';           -- (c)
