-- 041_feed_seed_film.sql — the Home feed's film/TV seed phrases split into
-- what the owner reads (Hollywood and US dealmaking, the major festivals)
-- and what they don't (international and regional TV/film markets).
-- Chat 3, Phase 2; owner, 2026-10-12.
--
-- WHY. The Love check (docs/feed_ranking.md decision 7, FAIL 2026-10-12)
-- showed that the single seed "film and TV dealmaking and talent deals" lifts
-- MIPCOM-style international sales and format deals onto page one of For
-- you, and the owner dismissed 8 of the 10 picked from it. Seed TEXT only: no
-- filter rule, so a story here still ranks; it is just no longer loved by
-- default.
--
-- What changes, in app_settings.seed_interests -> 'feed' -> 'home':
--   love            "film and TV dealmaking and talent deals" is replaced by
--                   "Hollywood and US film/TV dealmaking: studios, streamers,
--                   talent and production deals, box office business", and
--                   "major film festivals: Cannes, Venice, Berlin, Toronto,
--                   Sundance, and awards-season business" follows it.
--                   9 -> 10 phrases.
--   not_interested  "international and foreign-market TV and film sales,
--                   format deals, regional film markets (MIPCOM, Busan, Rio),
--                   and local box office outside the US" is appended.
--                   6 -> 7 phrases.
-- Sports seeds and every other key of seed_interests are untouched.
--
-- The worker embeds the seed phrases at every profile rebuild (feed/profile.py),
-- so the next `--rebuild` (or the nightly 04:00 one) uses the new text. No
-- code change.
--
-- SAFE TO RE-RUN, and never clobbers an edit: the lists are replaced only
-- when they read exactly as db/039 wrote them. Already the new lists: a
-- NOTICE and nothing changes. Anything else (an edit since db/039): an
-- exception, and nothing in this file is applied.
--
-- A self-test runs on paste (rolled back): the seed set reads back exactly
-- as written, through the same expression the worker reads
-- (core/ledger.py feed_seed_phrases), and sports is unchanged. A wrong
-- result raises and nothing in this file is applied.
--
-- Prerequisites: db/020 (app_settings), db/039 (the seeds).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- The seed phrases.

DO $$
DECLARE
    v_old_love jsonb := jsonb_build_array(
        'experiential hospitality: beach clubs, members'' clubs, waterfront venues, hotels',
        'yachts and marine: builds, sales, launches, marinas',
        'Miami and Biscayne Bay development; Florida coastal permitting',
        'venture capital, family offices, fund raises, notable deals',
        'sports business: team ownership, stakes, valuations, media rights, athlete businesses',
        'music business, artist management, creator economy deals',
        'film and TV dealmaking and talent deals',
        'luxury and lifestyle brands',
        'prediction markets and sports-betting regulation, Kalshi');
    v_new_love jsonb := jsonb_build_array(
        'experiential hospitality: beach clubs, members'' clubs, waterfront venues, hotels',
        'yachts and marine: builds, sales, launches, marinas',
        'Miami and Biscayne Bay development; Florida coastal permitting',
        'venture capital, family offices, fund raises, notable deals',
        'sports business: team ownership, stakes, valuations, media rights, athlete businesses',
        'music business, artist management, creator economy deals',
        'Hollywood and US film/TV dealmaking: studios, streamers, talent and production deals, box office business',
        'major film festivals: Cannes, Venice, Berlin, Toronto, Sundance, and awards-season business',
        'luxury and lifestyle brands',
        'prediction markets and sports-betting regulation, Kalshi');
    v_old_ni jsonb := jsonb_build_array(
        'celebrity gossip and relationships',
        'SEO listicles and filler',
        'partisan horse-race and culture-war politics (policy only when it moves money)',
        'generic "AI will change everything" takes',
        'crypto price chatter',
        'plain box-score recaps with no storyline');
    v_new_ni jsonb := v_old_ni || jsonb_build_array(
        'international and foreign-market TV and film sales, format deals, regional film markets (MIPCOM, Busan, Rio), and local box office outside the US');
    v_rows int := (SELECT count(*) FROM public.app_settings);
    v_love jsonb;
    v_ni   jsonb;
BEGIN
    IF v_rows <> 1 THEN
        RAISE EXCEPTION 'db/041: expected one app_settings row, found %. Nothing was applied.', v_rows;
    END IF;
    SELECT seed_interests -> 'feed' -> 'home' -> 'love', seed_interests -> 'feed' -> 'home' -> 'not_interested'
      INTO v_love, v_ni FROM public.app_settings;

    IF v_love = v_new_love AND v_ni = v_new_ni THEN
        RAISE NOTICE 'db/041: the home seeds already read as 041 writes them; nothing changed';
    ELSIF v_love = v_old_love AND v_ni = v_old_ni THEN
        UPDATE public.app_settings
           SET seed_interests = jsonb_set(jsonb_set(seed_interests,
                                    '{feed,home,love}', v_new_love),
                                    '{feed,home,not_interested}', v_new_ni),
               updated_at = now();
        RAISE NOTICE 'db/041: home seeds set: love 9 -> 10, not interested 6 -> 7';
    ELSE
        RAISE EXCEPTION 'db/041: the home seeds were edited since db/039 (love %, not interested %). Nothing was applied.',
            left(v_love::text, 200), left(v_ni::text, 200);
    END IF;
END
$$;

-- ===================================================================== 2
-- Self-test, rolled back: the seed set reads back as written.

DO $$
DECLARE
    v_feed jsonb;
BEGIN
    BEGIN
        -- The worker's read (core/ledger.py feed_seed_phrases).
        SELECT seed_interests -> 'feed' INTO v_feed FROM public.app_settings LIMIT 1;

        -- 1. Love, in order, exactly as written.
        IF v_feed -> 'home' -> 'love' IS DISTINCT FROM jsonb_build_array(
            'experiential hospitality: beach clubs, members'' clubs, waterfront venues, hotels',
            'yachts and marine: builds, sales, launches, marinas',
            'Miami and Biscayne Bay development; Florida coastal permitting',
            'venture capital, family offices, fund raises, notable deals',
            'sports business: team ownership, stakes, valuations, media rights, athlete businesses',
            'music business, artist management, creator economy deals',
            'Hollywood and US film/TV dealmaking: studios, streamers, talent and production deals, box office business',
            'major film festivals: Cannes, Venice, Berlin, Toronto, Sundance, and awards-season business',
            'luxury and lifestyle brands',
            'prediction markets and sports-betting regulation, Kalshi') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: home love reads back as %', left((v_feed -> 'home' -> 'love')::text, 400);
        END IF;
        -- 2. Not interested, in order, exactly as written.
        IF v_feed -> 'home' -> 'not_interested' IS DISTINCT FROM jsonb_build_array(
            'celebrity gossip and relationships',
            'SEO listicles and filler',
            'partisan horse-race and culture-war politics (policy only when it moves money)',
            'generic "AI will change everything" takes',
            'crypto price chatter',
            'plain box-score recaps with no storyline',
            'international and foreign-market TV and film sales, format deals, regional film markets (MIPCOM, Busan, Rio), and local box office outside the US') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: home not_interested reads back as %', left((v_feed -> 'home' -> 'not_interested')::text, 400);
        END IF;
        -- 3. The old film seed is gone; no phrase is blank or repeated.
        IF (v_feed -> 'home' -> 'love') ? 'film and TV dealmaking and talent deals' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the old film seed is still a love';
        END IF;
        IF EXISTS (SELECT 1 FROM jsonb_array_elements_text((v_feed -> 'home' -> 'love') || (v_feed -> 'home' -> 'not_interested')) e
                    GROUP BY e HAVING count(*) > 1 OR btrim(e) = '') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a seed phrase is blank or repeated';
        END IF;
        -- 4. Sports is unchanged (empty until Phase 3); home and sports are the only pillars.
        IF v_feed -> 'sports' IS DISTINCT FROM jsonb_build_object('love', '[]'::jsonb, 'not_interested', '[]'::jsonb) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: sports seeds changed: %', (v_feed -> 'sports')::text;
        END IF;
        IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(v_feed) k) IS DISTINCT FROM ARRAY['home', 'sports'] THEN
            RAISE EXCEPTION 'SELFTEST FAILED: seed pillars changed';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '041 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('041_feed_seed_film')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) home love 10, home not interested 7, sports 0 and 0
--   (b) the two new loves at positions 7 and 8; the new not interested last
--   (c) the migration_log row (and the NOTICE "041 self-test passed")
-- ---------------------------------------------------------------------------

SELECT jsonb_array_length(seed_interests -> 'feed' -> 'home' -> 'love') AS home_love,
       jsonb_array_length(seed_interests -> 'feed' -> 'home' -> 'not_interested') AS home_ni,
       jsonb_array_length(seed_interests -> 'feed' -> 'sports' -> 'love') AS sports_love,
       jsonb_array_length(seed_interests -> 'feed' -> 'sports' -> 'not_interested') AS sports_ni
  FROM app_settings;                                                                       -- (a)

SELECT seed_interests -> 'feed' -> 'home' -> 'love' -> 6 AS love_7,
       seed_interests -> 'feed' -> 'home' -> 'love' -> 7 AS love_8,
       seed_interests -> 'feed' -> 'home' -> 'not_interested' -> 6 AS ni_7
  FROM app_settings;                                                                       -- (b)

SELECT name, applied_at FROM migration_log WHERE name = '041_feed_seed_film';              -- (c)
