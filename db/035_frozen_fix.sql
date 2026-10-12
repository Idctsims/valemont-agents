-- 035_frozen_fix.sql — fix db/034's content_items freeze (Chat 3, Phase 1).
--
-- THE BUG. db/034 widened content_items_frozen() to allow clearing either
-- embedding, and compared the vectors with IS DISTINCT FROM. That needs
-- pgvector's `=` operator for halfvec, which lives in the extensions schema;
-- the function runs with search_path = '' so the operator is not found, and
-- EVERY update of content_items raised "operator does not exist", including
-- the nightly prune's embedding clear. Found by tests_live on 2026-10-12
-- before the worker ran the feed (ingest only inserts), so nothing was lost.
--
-- THE FIX. Compare the vectors' text forms: a cast to text uses the type's
-- own output function, with no operator lookup. The rule is unchanged: the
-- only change allowed to a row is clearing either embedding.
--
-- A self-test runs on paste, in a subtransaction that is rolled back (no row
-- survives): clearing both embeddings works; changing either embedding, or
-- the title, is refused. A wrong result raises and nothing is applied.
--
-- Prerequisites: db/032, db/034.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

CREATE OR REPLACE FUNCTION public.content_items_frozen()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF (NEW.embedding IS NOT NULL AND NEW.embedding::text IS DISTINCT FROM OLD.embedding::text)
       OR (NEW.title_embedding IS NOT NULL AND NEW.title_embedding::text IS DISTINCT FROM OLD.title_embedding::text)
       OR (to_jsonb(NEW) - 'embedding' - 'title_embedding')
          IS DISTINCT FROM (to_jsonb(OLD) - 'embedding' - 'title_embedding') THEN
        RAISE EXCEPTION 'content_items % is frozen: only clearing its embeddings is allowed', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

-- Self-test, rolled back.
DO $$
DECLARE
    v_src   bigint;
    v_story bigint;
    v_item  bigint;
    v_vec   text := '[' || array_to_string(array_fill(0.05::real, ARRAY[384]), ',') || ']';
    v_vec2  text := '[' || array_to_string(array_fill(0.06::real, ARRAY[384]), ',') || ']';
    v_refused int := 0;
BEGIN
    BEGIN
        SELECT id INTO v_src FROM public.sources WHERE slug = '_e2e' AND is_test;
        INSERT INTO public.stories (pillars, title, is_test) VALUES (ARRAY['home'], 'self-test 035', true)
        RETURNING id INTO v_story;
        INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id,
                                          embedding, title_embedding, is_test)
        VALUES (v_src, 'selftest:035', 'https://selftest.invalid/035', 'self-test item', now(), v_story,
                v_vec::extensions.halfvec, v_vec::extensions.halfvec, true)
        RETURNING id INTO v_item;

        -- Refused: changing an embedding, or anything else.
        BEGIN
            UPDATE public.content_items SET embedding = v_vec2::extensions.halfvec WHERE id = v_item;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN
            UPDATE public.content_items SET title_embedding = v_vec2::extensions.halfvec WHERE id = v_item;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN
            UPDATE public.content_items SET title = 'edited' WHERE id = v_item;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        IF v_refused <> 3 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 3 forbidden updates were refused', v_refused;
        END IF;

        -- Allowed: clearing one, then the other (the prune clears both at once).
        UPDATE public.content_items SET title_embedding = NULL WHERE id = v_item;
        UPDATE public.content_items SET embedding = NULL WHERE id = v_item;
        IF (SELECT embedding IS NOT NULL OR title_embedding IS NOT NULL FROM public.content_items WHERE id = v_item) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the embeddings were not cleared';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '035 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('035_frozen_fix')
ON CONFLICT (name) DO NOTHING;

-- Verify — expect: the function compares text forms; the migration_log row.
SELECT pg_get_functiondef('public.content_items_frozen()'::regprocedure) LIKE '%embedding::text IS DISTINCT FROM%'
       AS compares_text;
SELECT name, applied_at FROM migration_log WHERE name = '035_frozen_fix';
