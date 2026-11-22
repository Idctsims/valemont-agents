-- db/088_screening_vibe.sql
--
-- The Screening Room's "More like…" (owner, 2026-11-21; docs/screening.md §8).
-- The owner types a title, a person or a phrase; Sonnet names ~15 films and
-- shows with the same vibe; each is verified on TMDB; the verified list is
-- stored here. The same anchor within 30 days is served from this table with
-- no model call (the web's /api/screening/vibe), and the rows also count the
-- rate limit (10 in a rolling minute) and list the recent searches.
--
--   screening_vibe_searches  APPEND-ONLY, TRUNCATE-guarded: one row per
--     answered search. anchor_key is what was searched: movie:<tmdb id>,
--     tv:<tmdb id>, person:<tmdb id>, or text:<the typed phrase, lowercased>.
--     picks is the verified list as shown (1-20 objects). ai_usage_id links
--     the call's cost row (NULL in mock mode). is_test for a request with the
--     verified e2e marker only.
--
-- Access: the owner reads and inserts (RLS, is_owner()); never updates or
-- deletes; anon nothing; valemont_readonly reads.
--
-- Paste-by-hand: no BEGIN/COMMIT; the self-test at the end rolls itself back.

CREATE TABLE IF NOT EXISTS screening_vibe_searches (
    id           BIGSERIAL    PRIMARY KEY,
    anchor_key   TEXT         NOT NULL CONSTRAINT screening_vibe_anchor_key
                                CHECK (anchor_key ~ '^(movie|tv|person):[0-9]{1,10}$'
                                       OR anchor_key ~ '^text:.{2,120}$'),
    label        TEXT         NOT NULL CHECK (char_length(btrim(label)) BETWEEN 1 AND 200),
    picks        JSONB        NOT NULL CONSTRAINT screening_vibe_picks
                                CHECK (jsonb_typeof(picks) = 'array'
                                       AND jsonb_array_length(picks) BETWEEN 1 AND 20),
    model        TEXT         NOT NULL CHECK (char_length(model) BETWEEN 1 AND 60),
    ai_usage_id  BIGINT,
    is_test      BOOLEAN      NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS screening_vibe_anchor ON screening_vibe_searches (anchor_key, is_test, created_at DESC);
CREATE INDEX IF NOT EXISTS screening_vibe_created ON screening_vibe_searches (created_at);

DROP TRIGGER IF EXISTS screening_vibe_searches_stamp ON screening_vibe_searches;
CREATE TRIGGER screening_vibe_searches_stamp
    BEFORE INSERT ON screening_vibe_searches
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_stamp_created();
DROP TRIGGER IF EXISTS screening_vibe_searches_immutable ON screening_vibe_searches;
CREATE TRIGGER screening_vibe_searches_immutable
    BEFORE UPDATE OR DELETE ON screening_vibe_searches
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS screening_vibe_searches_no_truncate ON screening_vibe_searches;
CREATE TRIGGER screening_vibe_searches_no_truncate
    BEFORE TRUNCATE ON screening_vibe_searches
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

ALTER TABLE screening_vibe_searches ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON screening_vibe_searches FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE screening_vibe_searches_id_seq FROM anon, authenticated, PUBLIC;
GRANT SELECT, INSERT ON screening_vibe_searches TO authenticated;
GRANT USAGE ON SEQUENCE screening_vibe_searches_id_seq TO authenticated;
DROP POLICY IF EXISTS screening_vibe_owner_read ON screening_vibe_searches;
CREATE POLICY screening_vibe_owner_read ON screening_vibe_searches FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS screening_vibe_owner_insert ON screening_vibe_searches;
CREATE POLICY screening_vibe_owner_insert ON screening_vibe_searches FOR INSERT TO authenticated
    WITH CHECK ((SELECT public.is_owner()));
GRANT SELECT ON screening_vibe_searches TO valemont_readonly;


DO $$
DECLARE
    v_r  bigint;
    v_n  int := 0;
BEGIN
    BEGIN
        INSERT INTO public.screening_vibe_searches (anchor_key, label, picks, model, is_test)
        VALUES ('movie:98', 'Gladiator (2000)', '[{"tmdbId": 652, "kind": "movie", "name": "Troy"}]', 'mock', true)
        RETURNING id INTO v_r;
        IF (SELECT created_at FROM public.screening_vibe_searches WHERE id = v_r) IS DISTINCT FROM now() THEN
            RAISE EXCEPTION 'SELFTEST FAILED: created_at is not the database''s';
        END IF;
        INSERT INTO public.screening_vibe_searches (anchor_key, label, picks, model, is_test)
        VALUES ('text:90s london rom-coms', '90s London rom-coms', '[{"tmdbId": 509}]', 'mock', true);
        INSERT INTO public.screening_vibe_searches (anchor_key, label, picks, model, is_test)
        VALUES ('person:578', 'Ridley Scott', '[{"tmdbId": 98}]', 'mock', true);

        BEGIN
            UPDATE public.screening_vibe_searches SET label = 'rewritten' WHERE id = v_r;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.screening_vibe_searches WHERE id = v_r;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.screening_vibe_searches;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN  -- an empty list is never stored
            INSERT INTO public.screening_vibe_searches (anchor_key, label, picks, model) VALUES ('movie:98', 'x', '[]', 'mock');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- picks must be a list
            INSERT INTO public.screening_vibe_searches (anchor_key, label, picks, model) VALUES ('movie:98', 'x', '{"a": 1}', 'mock');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- an anchor key of no known kind
            INSERT INTO public.screening_vibe_searches (anchor_key, label, picks, model) VALUES ('show:98', 'x', '[{}]', 'mock');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- a text anchor under two characters
            INSERT INTO public.screening_vibe_searches (anchor_key, label, picks, model) VALUES ('text:a', 'x', '[{}]', 'mock');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- a 201-character label
            INSERT INTO public.screening_vibe_searches (anchor_key, label, picks, model)
            VALUES ('movie:98', repeat('x', 201), '[{}]', 'mock');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- a blank label
            INSERT INTO public.screening_vibe_searches (anchor_key, label, picks, model) VALUES ('movie:98', '   ', '[{}]', 'mock');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        IF v_n <> 9 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: screening_vibe_searches refusals (% of 9)', v_n;
        END IF;

        -- The access rules, as the roles hold them.
        IF has_table_privilege('anon', 'public.screening_vibe_searches', 'SELECT')
           OR has_table_privilege('authenticated', 'public.screening_vibe_searches', 'UPDATE')
           OR has_table_privilege('authenticated', 'public.screening_vibe_searches', 'DELETE')
           OR NOT has_table_privilege('authenticated', 'public.screening_vibe_searches', 'INSERT')
           OR NOT has_table_privilege('valemont_readonly', 'public.screening_vibe_searches', 'SELECT') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: screening_vibe_searches grants';
        END IF;
        IF (SELECT count(*) FROM pg_policies WHERE tablename = 'screening_vibe_searches') <> 2 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: screening_vibe_searches policies';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '088 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('088_screening_vibe')
ON CONFLICT (name) DO NOTHING;
