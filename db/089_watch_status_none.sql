-- db/089_watch_status_none.sql
--
-- Off the shelf (owner, 2026-11-22: the Screening Room's buttons as
-- Letterboxd's, where a lit Watched, Watching or Watchlist turns off when
-- tapped; docs/screening.md §9). watch_status stays append-only: taking a
-- title off its shelf is a new move with status 'none', never a delete.
--
--   1. watch_status.status gains 'none' (finished_at stays NULL for it, as
--      for want and watching: watch_status_finished is unchanged).
--   2. v_watch_current: the latest move per title, a latest 'none' left out.
--      Every shelf reader goes through it (the web's shelves, Upcoming,
--      release alerts, the title entities, ctx_screening, the worker), so
--      none of them changes.
--   3. v_title_state.shelf: nullif(latest, 'none'). `finished` is unchanged
--      (ever finished): an un-watched title stays out of For you, and the
--      Arsenal hook, once per title, has already fired.
-- The three AFTER INSERT triggers on watch_status act only on want,
-- watching or finished (title entity, Arsenal hook) or on any move (the
-- re-rank queue), so a 'none' move re-ranks and does nothing else.
--
-- Paste-by-hand: no BEGIN/COMMIT; the self-test at the end rolls itself back.

ALTER TABLE watch_status DROP CONSTRAINT IF EXISTS watch_status_status_check;
ALTER TABLE watch_status ADD CONSTRAINT watch_status_status_check
    CHECK (status IN ('want', 'watching', 'finished', 'none'));

CREATE OR REPLACE VIEW v_watch_current
WITH (security_invoker = true)
AS
SELECT cur.title_id, cur.status, cur.finished_at, cur.moved_at, cur.is_test
  FROM (SELECT DISTINCT ON (w.title_id)
               w.title_id, w.status, w.finished_at, w.created_at AS moved_at, t.is_test
          FROM public.watch_status w
          JOIN public.titles t ON t.id = w.title_id
         ORDER BY w.title_id, w.created_at DESC, w.id DESC) cur
 WHERE cur.status <> 'none';

CREATE OR REPLACE VIEW v_title_state
WITH (security_invoker = true)
AS
SELECT t.id AS title_id, t.is_test,
       nullif(r.reaction, 'none') AS reaction, r.reacted_at, coalesce(r.seen, false) AS seen, r.seen_pass,
       (sd.title_id IS NOT NULL) AS seed, sd.grp AS seed_group,
       nullif(w.status, 'none') AS shelf, coalesce(w.finished, false) AS finished, tr.reaction AS trailer_reaction
  FROM public.titles t
  LEFT JOIN LATERAL (
      SELECT (array_agg(x.reaction ORDER BY x.created_at DESC, x.id DESC))[1] AS reaction,
             max(x.created_at) AS reacted_at, bool_or(x.seen) AS seen,
             (array_agg(CASE WHEN x.reaction = 'love' THEN 'seen_loved'
                             WHEN x.reaction = 'not_interested' THEN 'seen_not_for_me'
                             ELSE 'not_seen' END ORDER BY x.created_at DESC, x.id DESC)
                  FILTER (WHERE x.origin = 'seen_pass'))[1] AS seen_pass
        FROM public.title_reactions x WHERE x.title_id = t.id) r ON true
  LEFT JOIN public.screening_seeds sd ON sd.title_id = t.id
  LEFT JOIN LATERAL (
      SELECT (array_agg(ws.status ORDER BY ws.created_at DESC, ws.id DESC))[1] AS status,
             bool_or(ws.status = 'finished') AS finished
        FROM public.watch_status ws WHERE ws.title_id = t.id) w ON true
  LEFT JOIN LATERAL (
      SELECT (array_agg(l.reaction ORDER BY l.reacted_at DESC))[1] AS reaction
        FROM public.title_trailers tt
        JOIN public.stories s ON s.lead_item_id = tt.item_id
        JOIN public.v_feed_reaction_latest l ON l.story_id = s.id AND l.pillar = 'screening'
       WHERE tt.title_id = t.id AND l.reaction IN ('love', 'neutral', 'not_interested')) tr ON true
 WHERE r.reacted_at IS NOT NULL OR sd.title_id IS NOT NULL OR w.status IS NOT NULL OR tr.reaction IS NOT NULL;


DO $$
DECLARE
    v_owner  uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_a      bigint;
    v_b      bigint;
    v_n      int := 0;
BEGIN
    BEGIN
        INSERT INTO public.titles (tmdb_id, kind, name, is_test) VALUES (989089001, 'movie', 'self-test 089 a', true)
        RETURNING id INTO v_a;
        INSERT INTO public.titles (tmdb_id, kind, name, is_test) VALUES (989089002, 'tv', 'self-test 089 b', true)
        RETURNING id INTO v_b;

        -- Want, then off the shelf: gone from the shelves, no shelf in the state.
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_a, 'want');
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_a, 'none');
        IF EXISTS (SELECT 1 FROM public.v_watch_current WHERE title_id = v_a) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an off-the-shelf title is still current';
        END IF;
        IF (SELECT shelf FROM public.v_title_state WHERE title_id = v_a) IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: v_title_state shows a shelf for an off-the-shelf title';
        END IF;
        IF (SELECT finished_at FROM public.watch_status WHERE title_id = v_a AND status = 'none') IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a none move carries finished_at';
        END IF;

        -- Back on a shelf after it.
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_a, 'watching');
        IF (SELECT status FROM public.v_watch_current WHERE title_id = v_a) IS DISTINCT FROM 'watching'
           OR (SELECT shelf FROM public.v_title_state WHERE title_id = v_a) IS DISTINCT FROM 'watching' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a title back on a shelf';
        END IF;

        -- Finished, then off: no shelf, still ever finished.
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_b, 'finished');
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_b, 'none');
        IF EXISTS (SELECT 1 FROM public.v_watch_current WHERE title_id = v_b)
           OR (SELECT shelf FROM public.v_title_state WHERE title_id = v_b) IS NOT NULL
           OR NOT (SELECT finished FROM public.v_title_state WHERE title_id = v_b) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: finished then off (no shelf, ever finished)';
        END IF;

        -- Still append-only; an unknown status refused.
        BEGIN
            UPDATE public.watch_status SET status = 'want' WHERE title_id = v_a;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_a, 'dropped');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        IF v_n <> 2 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: watch_status refusals (% of 2)', v_n;
        END IF;

        -- The views keep db/052's and db/053's grants (CREATE OR REPLACE keeps them).
        IF has_table_privilege('anon', 'public.v_watch_current', 'SELECT')
           OR has_table_privilege('anon', 'public.v_title_state', 'SELECT')
           OR NOT has_table_privilege('authenticated', 'public.v_watch_current', 'SELECT')
           OR NOT has_table_privilege('authenticated', 'public.v_title_state', 'SELECT')
           OR has_table_privilege('authenticated', 'public.v_watch_current', 'INSERT,UPDATE,DELETE')
           OR NOT has_table_privilege('valemont_readonly', 'public.v_watch_current', 'SELECT')
           OR NOT has_table_privilege('valemont_readonly', 'public.v_title_state', 'SELECT') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: view grants';
        END IF;

        -- The readers that go through the views still answer.
        PERFORM public.ctx_screening();

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '089 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('089_watch_status_none')
ON CONFLICT (name) DO NOTHING;
