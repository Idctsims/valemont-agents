-- 062_arsenal_title_hook.sql — The Arsenal, Phase 3: the Screening Room hook
-- (Chat 5 Phase 3; owner decisions Oct 15; docs/arsenal.md §10).
--
-- Finishing a title (a film or a show) drafts its references into the
-- Arsenal, ONCE per title, ever: a re-watch drafts nothing new.
--
-- What it changes:
--   1. arsenal_refs: origin_kind gains 'title_hook'; a title_hook card carries
--      source_title_id and only it does (CHECK). The reference guard (db/059's)
--      adds: a title_hook card enters as 'pool', with its title's is_test, and
--      only while that title's hook is still queued (or failed and re-run by
--      hand): no card is ever added to a drafted hook.
--   2. arsenal_title_hooks: ONE row per title EVER (UNIQUE title_id). It is
--      append-only in identity and settles once: queued -> drafted | failed,
--      with ref_ids, word_id, ai_usage_id and a note set by that move. A
--      drafted hook never changes again. A failed hook moves again only by a
--      hand re-run (scripts.arsenal_title_hook --rerun), which first writes an
--      arsenal_title_hook_reruns row (APPEND-ONLY) in the same transaction.
--      No DELETE, no TRUNCATE.
--   3. watch_status AFTER INSERT (SECURITY DEFINER): a move to 'finished' on a
--      title with no hook writes the hook (queued) and, for a real title, ONE
--      job_queue row of kind 'arsenal_title_hook'. A TEST title's hook is
--      written (is_test) but never queued: no worker, no AI; its cards come
--      only through add_test_title_hook_cards (the verified e2e marker).
--   4. ctx_arsenal gains recent_title_cards (the last 5 hooks: title, state,
--      card counts); ctx_screening's finished titles gain arsenal_cards (N) or
--      arsenal 'drafting'.
--   5. Access: RLS on both tables; anon nothing; the owner reads (select
--      only); only the worker (and the trigger) writes; valemont_readonly
--      reads both.
--
-- Titles already Finished before this file get no hook now (no backfill: no
-- AI spend on old titles). Such a title drafts the first time it is marked
-- Finished again, since it has never drafted.
--
-- A self-test runs on paste (section 8), rolled back, and raises on any wrong
-- result, so a bad paste applies nothing.
-- ORDER (db/055's lesson): every table a trigger function reads is created
-- before any statement that fires it. FIXTURES (db/053's): no explicit NULL
-- into a NOT NULL column. TRUNCATE (db/042's): every referencing table named.
-- Prerequisites: db/020 (job_queue, ai_usage, is_owner), db/051 (titles,
-- watch_status, e2e_marker_ok), db/053, db/054–db/059.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- PASTE BEFORE THE MERGE: the worker and the web read the new schema.

-- ===================================================================== 1
-- arsenal_title_hooks and its re-run record (created first: the reference
-- guard below reads the hooks).

CREATE TABLE IF NOT EXISTS arsenal_title_hooks (
    id               BIGSERIAL    PRIMARY KEY,
    title_id         BIGINT       NOT NULL UNIQUE REFERENCES titles (id) ON DELETE RESTRICT,
    -- The 'finished' move that triggered it.
    watch_status_id  BIGINT       NOT NULL UNIQUE REFERENCES watch_status (id) ON DELETE RESTRICT,
    status           TEXT         NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'drafted', 'failed')),
    ref_ids          BIGINT[]     NOT NULL DEFAULT '{}' CHECK (cardinality(ref_ids) <= 5),
    word_id          BIGINT       REFERENCES arsenal_words (id) ON DELETE RESTRICT,
    -- The draft's Sonnet call (verification's Haiku calls are their own rows).
    ai_usage_id      BIGINT       REFERENCES ai_usage (id) ON DELETE RESTRICT,
    note             TEXT         CHECK (note IS NULL OR char_length(note) <= 500),
    is_test          BOOLEAN      NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    settled_at       TIMESTAMPTZ,
    CONSTRAINT arsenal_title_hooks_settled CHECK ((status = 'queued') = (settled_at IS NULL)),
    CONSTRAINT arsenal_title_hooks_drafted CHECK (status <> 'drafted' OR cardinality(ref_ids) >= 1),
    CONSTRAINT arsenal_title_hooks_empty CHECK (status = 'drafted' OR (ref_ids = '{}' AND word_id IS NULL))
);
CREATE INDEX IF NOT EXISTS arsenal_title_hooks_recent ON arsenal_title_hooks (created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS arsenal_title_hook_reruns (
    id          BIGSERIAL    PRIMARY KEY,
    hook_id     BIGINT       NOT NULL REFERENCES arsenal_title_hooks (id) ON DELETE RESTRICT,
    old_status  TEXT         NOT NULL CHECK (old_status = 'failed'),
    by_whom     TEXT         NOT NULL CHECK (char_length(btrim(by_whom)) BETWEEN 1 AND 200),
    -- The database role that ran it.
    role        TEXT         NOT NULL DEFAULT current_user,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS arsenal_title_hook_reruns_hook ON arsenal_title_hook_reruns (hook_id, created_at DESC);

CREATE OR REPLACE FUNCTION public.arsenal_title_hook_reruns_stamp()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.created_at := now();
    NEW.role := current_user;
    IF (SELECT h.status FROM public.arsenal_title_hooks h WHERE h.id = NEW.hook_id) IS DISTINCT FROM 'failed' THEN
        RAISE EXCEPTION 'arsenal_title_hook_reruns: only a failed hook is re-run by hand';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS arsenal_title_hook_reruns_stamp ON arsenal_title_hook_reruns;
CREATE TRIGGER arsenal_title_hook_reruns_stamp
    BEFORE INSERT ON arsenal_title_hook_reruns
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_title_hook_reruns_stamp();
DROP TRIGGER IF EXISTS arsenal_title_hook_reruns_immutable ON arsenal_title_hook_reruns;
CREATE TRIGGER arsenal_title_hook_reruns_immutable
    BEFORE UPDATE OR DELETE ON arsenal_title_hook_reruns
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS arsenal_title_hook_reruns_no_truncate ON arsenal_title_hook_reruns;
CREATE TRIGGER arsenal_title_hook_reruns_no_truncate
    BEFORE TRUNCATE ON arsenal_title_hook_reruns
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- The hook's lifecycle. INSERT: queued, empty, its title's is_test. UPDATE:
-- identity never changes; queued settles once to drafted or failed; a failed
-- hook moves again only with a re-run row written in this same transaction
-- (created_at = now(), the transaction's start); drafted never changes.
CREATE OR REPLACE FUNCTION public.arsenal_title_hooks_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := now();
        NEW.settled_at := NULL;
        NEW.is_test := coalesce((SELECT t.is_test FROM public.titles t WHERE t.id = NEW.title_id), false);
        IF NEW.status <> 'queued' OR NEW.ref_ids <> '{}' OR NEW.word_id IS NOT NULL OR NEW.ai_usage_id IS NOT NULL THEN
            RAISE EXCEPTION 'arsenal_title_hooks: a hook enters queued and empty';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.watch_status w
                        WHERE w.id = NEW.watch_status_id AND w.title_id = NEW.title_id AND w.status = 'finished') THEN
            RAISE EXCEPTION 'arsenal_title_hooks: a hook comes from its title''s move to finished';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'arsenal_title_hooks: a hook is never deleted (once per title, ever)';
    END IF;
    -- UPDATE.
    IF NEW.id <> OLD.id OR NEW.title_id <> OLD.title_id OR NEW.watch_status_id <> OLD.watch_status_id
       OR NEW.is_test <> OLD.is_test OR NEW.created_at <> OLD.created_at THEN
        RAISE EXCEPTION 'arsenal_title_hooks: a hook''s identity never changes';
    END IF;
    IF OLD.status = 'drafted' THEN
        RAISE EXCEPTION 'arsenal_title_hooks: a drafted hook never changes (once per title)';
    END IF;
    IF OLD.status = 'failed' AND NOT EXISTS (
           SELECT 1 FROM public.arsenal_title_hook_reruns r WHERE r.hook_id = OLD.id AND r.created_at = now()) THEN
        RAISE EXCEPTION 'arsenal_title_hooks: a failed hook moves only by a hand re-run (arsenal_title_hook_reruns)';
    END IF;
    IF NEW.status NOT IN ('drafted', 'failed') THEN
        RAISE EXCEPTION 'arsenal_title_hooks: a hook settles to drafted or failed, never %', NEW.status;
    END IF;
    NEW.settled_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS arsenal_title_hooks_guard ON arsenal_title_hooks;
CREATE TRIGGER arsenal_title_hooks_guard
    BEFORE INSERT OR UPDATE OR DELETE ON arsenal_title_hooks
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_title_hooks_guard();
DROP TRIGGER IF EXISTS arsenal_title_hooks_no_truncate ON arsenal_title_hooks;
CREATE TRIGGER arsenal_title_hooks_no_truncate
    BEFORE TRUNCATE ON arsenal_title_hooks
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 2
-- arsenal_refs: the 'title_hook' origin.

ALTER TABLE arsenal_refs DROP CONSTRAINT IF EXISTS arsenal_refs_origin_kind_check;
ALTER TABLE arsenal_refs ADD CONSTRAINT arsenal_refs_origin_kind_check
    CHECK (origin_kind IN ('pool', 'seed', 'arm_me', 'title_hook'));
ALTER TABLE arsenal_refs DROP CONSTRAINT IF EXISTS arsenal_refs_title_hook;
ALTER TABLE arsenal_refs ADD CONSTRAINT arsenal_refs_title_hook
    CHECK ((origin_kind = 'title_hook') = (source_title_id IS NOT NULL));
CREATE INDEX IF NOT EXISTS arsenal_refs_title ON arsenal_refs (source_title_id) WHERE source_title_id IS NOT NULL;

-- db/059's reference guard, plus the title_hook rules at INSERT. Every other
-- rule is unchanged.
CREATE OR REPLACE FUNCTION public.arsenal_refs_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_rev boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := now();
        NEW.verified_at := CASE WHEN NEW.verification <> 'pending' THEN now() END;
        IF NEW.status NOT IN ('pool', 'seed', 'arm') THEN
            RAISE EXCEPTION 'arsenal_refs: a reference enters as pool, seed or arm, never %', NEW.status;
        END IF;
        IF NOT NEW.is_test AND NEW.verification <> 'pending' THEN
            RAISE EXCEPTION 'arsenal_refs: a real reference enters pending; verification is its own step';
        END IF;
        -- db/062: a finished title's card. (With no title, the
        -- arsenal_refs_title_hook CHECK refuses it.)
        IF NEW.origin_kind = 'title_hook' AND NEW.source_title_id IS NOT NULL THEN
            IF NEW.status <> 'pool' THEN
                RAISE EXCEPTION 'arsenal_refs: a title_hook card enters the pool';
            END IF;
            IF NEW.is_test IS DISTINCT FROM (SELECT t.is_test FROM public.titles t WHERE t.id = NEW.source_title_id) THEN
                RAISE EXCEPTION 'arsenal_refs: a title_hook card is test exactly when its title is';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM public.arsenal_title_hooks h
                            WHERE h.title_id = NEW.source_title_id AND h.status IN ('queued', 'failed')) THEN
                RAISE EXCEPTION 'arsenal_refs: a title_hook card needs its title''s hook still to draft (once per title)';
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'arsenal_refs: a reference is never deleted (status rejected retires one)';
    END IF;
    -- UPDATE.
    IF NEW.id <> OLD.id OR NEW.canonical_key <> OLD.canonical_key OR NEW.is_test <> OLD.is_test
       OR NEW.origin_kind <> OLD.origin_kind OR NEW.created_at <> OLD.created_at
       OR NEW.pool_run_id IS DISTINCT FROM OLD.pool_run_id OR NEW.source_title_id IS DISTINCT FROM OLD.source_title_id THEN
        RAISE EXCEPTION 'arsenal_refs: a reference''s identity never changes';
    END IF;
    -- The embedding is set once (compared as text: no pgvector operator here).
    IF OLD.embedding IS NOT NULL AND NEW.embedding::text IS DISTINCT FROM OLD.embedding::text THEN
        RAISE EXCEPTION 'arsenal_refs: the embedding is set once';
    END IF;
    -- The one re-verification (db/055): only inside arsenal_reverify_ref, for
    -- an unverified pool reference, matching the row it just recorded.
    v_rev := false;
    IF OLD.status = 'pool' AND OLD.verification = 'unverified' AND NEW.status = OLD.status
       AND coalesce(current_setting('arsenal.reverify', true), '') = 'ref:' || OLD.id THEN
        v_rev := EXISTS (SELECT 1 FROM public.arsenal_reverifications v
                          WHERE v.ref_id = OLD.id AND v.new_verification = NEW.verification
                            AND v.new_note IS NOT DISTINCT FROM NEW.verification_note);
    END IF;
    IF v_rev THEN
        IF NEW.verification = 'pending' THEN
            RAISE EXCEPTION 'arsenal_refs: a re-verification settles; it never returns to pending';
        END IF;
        NEW.verified_at := now();
        RETURN NEW;
    END IF;
    IF (NEW.category, NEW.title, NEW.origin, NEW.meaning, NEW.deploy_line, NEW.room, NEW.source_work, NEW.quote,
        NEW.quote_by, NEW.source_kind, NEW.source_page, NEW.source_url)
       IS DISTINCT FROM
       (OLD.category, OLD.title, OLD.origin, OLD.meaning, OLD.deploy_line, OLD.room, OLD.source_work, OLD.quote,
        OLD.quote_by, OLD.source_kind, OLD.source_page, OLD.source_url)
       AND OLD.verification <> 'pending' THEN
        RAISE EXCEPTION 'arsenal_refs: a reference''s card changes only while it is pending verification';
    END IF;
    IF OLD.verification <> 'pending' AND (NEW.verification, NEW.verification_note, NEW.verified_at)
                                          IS DISTINCT FROM (OLD.verification, OLD.verification_note, OLD.verified_at) THEN
        RAISE EXCEPTION 'arsenal_refs: verification settles once';
    END IF;
    IF OLD.verification = 'pending' AND NEW.verification <> 'pending' THEN
        NEW.verified_at := now();
    END IF;
    IF NEW.status <> OLD.status THEN
        IF OLD.status <> 'pool' THEN
            RAISE EXCEPTION 'arsenal_refs: a % reference never changes status', OLD.status;
        END IF;
        IF NEW.status = 'dropped'
           AND NOT EXISTS (SELECT 1 FROM public.arsenal_drops d WHERE NEW.id = ANY (d.ref_ids)) THEN
            RAISE EXCEPTION 'arsenal_refs: only a drop marks a reference dropped';
        END IF;
        IF NEW.status NOT IN ('dropped', 'rejected') THEN
            RAISE EXCEPTION 'arsenal_refs: a pool reference becomes dropped or rejected, never %', NEW.status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- ===================================================================== 3
-- watch_status: a move to finished writes the title's hook, once ever.

CREATE OR REPLACE FUNCTION public.watch_status_title_hook()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_test boolean;
    v_hook bigint;
BEGIN
    IF NEW.status <> 'finished' THEN
        RETURN NULL;
    END IF;
    SELECT t.is_test INTO v_test FROM public.titles t WHERE t.id = NEW.title_id;
    INSERT INTO public.arsenal_title_hooks (title_id, watch_status_id)
    VALUES (NEW.title_id, NEW.id)
    ON CONFLICT (title_id) DO NOTHING
    RETURNING id INTO v_hook;
    -- A re-watch (the title already has a hook) drafts nothing; a test title's
    -- hook is never queued (its cards come only from the e2e helper).
    IF v_hook IS NOT NULL AND NOT coalesce(v_test, false) THEN
        INSERT INTO public.job_queue (kind, payload)
        VALUES ('arsenal_title_hook', jsonb_build_object('hook_id', v_hook, 'title_id', NEW.title_id));
    END IF;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS watch_status_title_hook ON watch_status;
CREATE TRIGGER watch_status_title_hook
    AFTER INSERT ON watch_status
    FOR EACH ROW EXECUTE FUNCTION public.watch_status_title_hook();

-- ===================================================================== 4
-- Context.

-- db/059's ctx_arsenal, plus recent_title_cards: the last 5 real hooks.
CREATE OR REPLACE FUNCTION public.ctx_arsenal()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH drops AS (
        SELECT d.id, d.drop_date, d.ref_ids, d.word_id
          FROM public.arsenal_drops d
         WHERE NOT d.is_test AND d.drop_date BETWEEN public.owner_today() - 7 AND public.owner_today()
    ),
    cards AS (
        SELECT d.id AS drop_id, s.ord, r.title, r.category, r.room, r.source_work, r.meaning, r.deploy_line
          FROM drops d
          CROSS JOIN LATERAL unnest(d.ref_ids) WITH ORDINALITY AS s(ref_id, ord)
          JOIN public.arsenal_refs r ON r.id = s.ref_id
    ),
    saved AS (
        SELECT s.ref_id, s.word_id, s.saved_at FROM public.v_arsenal_saved s WHERE NOT s.is_test
    ),
    quiz AS (
        SELECT z.week_start, jsonb_array_length(z.questions) AS questions,
               count(v.id) AS answered, count(v.id) FILTER (WHERE v.correct) AS right_answers
          FROM public.arsenal_quizzes z
          JOIN public.arsenal_quiz_reviews v ON v.quiz_id = z.id
         WHERE NOT z.is_test
         GROUP BY z.id, z.week_start, z.questions
         ORDER BY z.week_start DESC, z.id DESC
         LIMIT 1
    ),
    hooks AS (
        SELECT h.id, h.status, h.ref_ids, h.created_at, t.name, t.kind
          FROM public.arsenal_title_hooks h
          JOIN public.titles t ON t.id = h.title_id
         WHERE NOT h.is_test
         ORDER BY h.created_at DESC, h.id DESC
         LIMIT 5
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'about', 'The Arsenal: a daily drop of three references (each with where it comes from, what it means, '
                 'a line to deploy and the room it plays in: boardroom, pitch, negotiation or homies) and a Power '
                 'Word. "today" is today''s drop; "last_7_days" the week before; "weights" how the owner''s '
                 'reactions and saves tilt categories and rooms (1.0 is neutral); "saved_count" and '
                 '"recent_saves" are My Arsenal (what the owner saved, including Arm Me cards); "last_quiz" is '
                 'the latest Monday quiz on the saved cards; "recent_title_cards" are the cards drafted from '
                 'films and shows the owner finished in the Screening Room (cards: drafted; checked: verified or '
                 'corrected).',
        'today', (SELECT jsonb_build_object(
                     'date', d.drop_date,
                     'refs', (SELECT jsonb_agg(jsonb_build_object(
                                  'title', c.title, 'category', c.category, 'room', c.room,
                                  'source_work', c.source_work, 'meaning', c.meaning, 'deploy_line', c.deploy_line)
                                  ORDER BY c.ord) FROM cards c WHERE c.drop_id = d.id),
                     'power_word', (SELECT jsonb_build_object('word', w.lemma, 'meaning', w.meaning, 'room', w.room,
                                                              'sentence', w.sentence)
                                      FROM public.arsenal_words w WHERE w.id = d.word_id))
                    FROM drops d WHERE d.drop_date = public.owner_today()),
        'last_7_days', coalesce((SELECT jsonb_agg(jsonb_build_object(
                                     'date', d.drop_date,
                                     'refs', (SELECT jsonb_agg(c.title ORDER BY c.ord) FROM cards c WHERE c.drop_id = d.id),
                                     'power_word', (SELECT w.lemma FROM public.arsenal_words w WHERE w.id = d.word_id))
                                     ORDER BY d.drop_date DESC)
                                   FROM drops d WHERE d.drop_date < public.owner_today()), '[]'::jsonb),
        'weights', (SELECT jsonb_object_agg(x.dim || ':' || x.key, round(x.weight::numeric, 2))
                      FROM public.arsenal_weights() x),
        'saved_count', (SELECT count(*) FROM saved),
        'recent_saves', coalesce((SELECT jsonb_agg(jsonb_build_object(
                                      'kind', CASE WHEN x.ref_id IS NOT NULL THEN 'reference' ELSE 'word' END,
                                      'title', coalesce(r.title, w.lemma), 'room', coalesce(r.room, w.room),
                                      'saved_at', x.saved_at) ORDER BY x.saved_at DESC)
                                    FROM (SELECT * FROM saved ORDER BY saved_at DESC LIMIT 5) x
                                    LEFT JOIN public.arsenal_refs r ON r.id = x.ref_id
                                    LEFT JOIN public.arsenal_words w ON w.id = x.word_id), '[]'::jsonb),
        'last_quiz', (SELECT jsonb_build_object('week_start', q.week_start,
                                                'score', q.right_answers || '/' || q.questions,
                                                'answered', q.answered, 'questions', q.questions)
                        FROM quiz q),
        'recent_title_cards', coalesce((SELECT jsonb_agg(jsonb_build_object(
                                            'title', h.name, 'kind', h.kind, 'state', h.status,
                                            'finished', h.created_at::date,
                                            'cards', cardinality(h.ref_ids),
                                            'checked', (SELECT count(*) FROM public.arsenal_refs r
                                                         WHERE r.id = ANY (h.ref_ids)
                                                           AND r.verification IN ('verified', 'corrected')),
                                            'card_titles', (SELECT jsonb_agg(r.title ORDER BY r.id)
                                                              FROM public.arsenal_refs r WHERE r.id = ANY (h.ref_ids)))
                                            ORDER BY h.created_at DESC, h.id DESC)
                                          FROM hooks h), '[]'::jsonb)
    ));
$$;

-- db/053's ctx_screening, with each finished title's Arsenal cards:
-- arsenal_cards (N, once drafted) or arsenal 'drafting' (queued).
CREATE OR REPLACE FUNCTION public.ctx_screening()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH cur AS (
        SELECT c.title_id, c.status, c.finished_at, c.moved_at, t.name, t.kind, t.release_date, t.next_episode_date,
               t.tmdb_id, t.tonight
          FROM public.v_watch_current c
          JOIN public.titles t ON t.id = c.title_id
         WHERE NOT c.is_test
    ),
    soon AS (
        SELECT c.*, coalesce(
                   CASE WHEN c.next_episode_date >= public.owner_today() THEN c.next_episode_date END,
                   CASE WHEN c.release_date >= public.owner_today() THEN c.release_date END) AS on_date
          FROM cur c
         WHERE c.status IN ('want', 'watching')
    ),
    trailers AS (
        SELECT v.id, v.title, v.lead_url, v.first_seen_at, t.name AS matched, t.kind, t.tmdb_id
          FROM public.v_feed_stories v
          JOIN public.title_trailers tt ON tt.item_id = v.lead_item_id AND tt.status = 'matched'
          JOIN public.titles t ON t.id = tt.title_id
         WHERE 'screening' = ANY (v.pillars)
         ORDER BY v.first_seen_at DESC, v.id DESC
         LIMIT 10
    ),
    loves AS (
        SELECT t.name, t.kind, s.reacted_at, s.finished
          FROM public.v_title_state s JOIN public.titles t ON t.id = s.title_id
         WHERE NOT s.is_test AND s.reaction = 'love' AND NOT s.seed
           AND s.reacted_at > now() - interval '30 days'
         ORDER BY s.reacted_at DESC
         LIMIT 10
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'about', 'Screening Room: the owner''s shelves (watching, want, finished), what is coming up in the next 30 '
                 'days on them, the newest trailers matched to a title, the For you picks (each with the loved '
                 'title it is like), today''s releases and recent loves. "tonight" lists the owner''s services a '
                 'title streams on in the US (Netflix, HBO Max, Paramount+ with Showtime, Starz, Prime Video). For '
                 '"what should I watch tonight", suggest titles with a tonight service first: a Watching title, '
                 'then a For you pick. A finished title''s "arsenal_cards" is how many Arsenal cards it drafted '
                 '("arsenal": "drafting" while they are being written).',
        'watching', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                         'title', c.name, 'kind', c.kind, 'next_episode', c.next_episode_date,
                         'tonight', CASE WHEN cardinality(c.tonight) > 0 THEN to_jsonb(c.tonight) END))
                       ORDER BY c.moved_at DESC) FROM cur c WHERE c.status = 'watching'), '[]'::jsonb),
        'want', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                     'title', c.name, 'kind', c.kind, 'release_date', c.release_date, 'next_episode', c.next_episode_date,
                     'tonight', CASE WHEN cardinality(c.tonight) > 0 THEN to_jsonb(c.tonight) END))
                   ORDER BY c.moved_at DESC) FROM cur c WHERE c.status = 'want'), '[]'::jsonb),
        'finished_recent', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                                         'title', f.name, 'kind', f.kind, 'finished', f.finished_at::date,
                                         'arsenal_cards', CASE WHEN h.status = 'drafted' THEN cardinality(h.ref_ids) END,
                                         'arsenal', CASE WHEN h.status = 'queued' THEN 'drafting' END))
                                                      ORDER BY f.finished_at DESC)
                                       FROM (SELECT * FROM cur WHERE status = 'finished'
                                              ORDER BY finished_at DESC LIMIT 20) f
                                       LEFT JOIN public.arsenal_title_hooks h ON h.title_id = f.title_id), '[]'::jsonb),
        'upcoming_30d', coalesce((SELECT jsonb_agg(jsonb_build_object('title', s.name, 'kind', s.kind,
                                                                      'on', s.on_date, 'shelf', s.status)
                                                   ORDER BY s.on_date)
                                    FROM soon s WHERE s.on_date <= public.owner_today() + 30), '[]'::jsonb),
        'new_trailers', coalesce((SELECT jsonb_agg(jsonb_build_object('trailer', public.ctx_trim(t.title, 160),
                                                                      'title', t.matched, 'kind', t.kind)
                                                   ORDER BY t.first_seen_at DESC, t.id DESC)
                                    FROM trailers t), '[]'::jsonb),
        'for_you_top5', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                                      'title', f.name, 'kind', f.kind, 'because_you_loved', f.because_name,
                                      'tonight', CASE WHEN cardinality(f.tonight) > 0 THEN to_jsonb(f.tonight) END))
                                    ORDER BY f.pos)
                                    FROM public.screening_for_you(5, false) f), '[]'::jsonb),
        'today', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                               'title', e.name, 'kind', e.kind, 'what', e.what, 'on', e.service)))
                             FROM public.screening_events_on(NULL) e), '[]'::jsonb),
        'recent_loves', coalesce((SELECT jsonb_agg(jsonb_build_object('title', l.name, 'kind', l.kind,
                                                                      'finished', l.finished)
                                                   ORDER BY l.reacted_at DESC) FROM loves l), '[]'::jsonb)
    ));
$$;

-- ===================================================================== 5
-- Test rows (e2e). Owner only, the verified e2e marker (db/051 e2e_marker_ok).

-- Drafts a TEST title's hook: p_cards test cards (title_hook, pool,
-- verified with a test source URL; the first one 'unverified' when
-- p_cards >= 3, so the spec sees both tags) and one test word, then settles
-- the hook drafted. The title must be test and already Finished (its hook
-- queued). Returns the hook id.
CREATE OR REPLACE FUNCTION public.add_test_title_hook_cards(p_marker text, p_title_id bigint, p_cards integer DEFAULT 3)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_hook   bigint;
    v_name   text;
    v_refs   bigint[] := '{}';
    v_word   bigint;
    v_id     bigint;
    v_tag    text := md5(random()::text);
    v_cats   text[] := ARRAY['film_tv', 'finance_business', 'history_military', 'music_culture', 'sports'];
    v_rooms  text[] := ARRAY['boardroom', 'pitch', 'negotiation', 'homies', 'boardroom'];
BEGIN
    IF NOT public.e2e_marker_ok(p_marker) THEN
        RAISE EXCEPTION 'add_test_title_hook_cards: the owner and a verified test marker only';
    END IF;
    SELECT h.id, t.name INTO v_hook, v_name
      FROM public.arsenal_title_hooks h JOIN public.titles t ON t.id = h.title_id
     WHERE h.title_id = p_title_id AND t.is_test AND h.status = 'queued';
    IF v_hook IS NULL THEN
        RAISE EXCEPTION 'add_test_title_hook_cards: title % is not a finished test title with a queued hook', p_title_id;
    END IF;
    IF p_cards NOT BETWEEN 1 AND 5 THEN
        RAISE EXCEPTION 'add_test_title_hook_cards: 1 to 5 cards';
    END IF;
    FOR i IN 1 .. p_cards LOOP
        INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work,
                                         source_kind, source_page, source_url, status, verification, verification_note,
                                         origin_kind, source_title_id, is_test)
        VALUES ('e2e_hook_' || left(v_tag, 12) || '_' || i, v_cats[i], 'Hook card ' || i || ' of ' || left(v_name, 80),
                'A test origin, from the finished test title.', 'A test meaning.', 'A test line to deploy.',
                v_rooms[i], left(v_name, 150) || ' (test)', 'wikipedia', 'Test', 'https://e2e.invalid/hook/' || i,
                'pool', CASE WHEN i = 1 AND p_cards >= 3 THEN 'unverified' ELSE 'verified' END,
                CASE WHEN i = 1 AND p_cards >= 3 THEN 'insufficient: a test reason' END,
                'title_hook', p_title_id, true)
        RETURNING id INTO v_id;
        v_refs := v_refs || v_id;
    END LOOP;
    INSERT INTO public.arsenal_words (lemma, meaning, room, sentence, is_test)
    VALUES ('e2e-hook-' || left(v_tag, 12), 'A test meaning.', 'boardroom', 'A test sentence with e2e-hook-' || left(v_tag, 12) || '.', true)
    RETURNING id INTO v_word;
    UPDATE public.arsenal_title_hooks
       SET status = 'drafted', ref_ids = v_refs, word_id = v_word, note = 'test cards (add_test_title_hook_cards)'
     WHERE id = v_hook;
    RETURN v_hook;
END;
$$;

-- ===================================================================== 6
-- Access.

ALTER TABLE arsenal_title_hooks ENABLE ROW LEVEL SECURITY;
ALTER TABLE arsenal_title_hook_reruns ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON arsenal_title_hooks, arsenal_title_hook_reruns FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE arsenal_title_hooks_id_seq, arsenal_title_hook_reruns_id_seq FROM PUBLIC, anon, authenticated;
GRANT SELECT ON arsenal_title_hooks, arsenal_title_hook_reruns TO authenticated;

DROP POLICY IF EXISTS arsenal_title_hooks_owner_read ON arsenal_title_hooks;
CREATE POLICY arsenal_title_hooks_owner_read ON arsenal_title_hooks FOR SELECT TO authenticated
    USING (public.is_owner());
DROP POLICY IF EXISTS arsenal_title_hook_reruns_owner_read ON arsenal_title_hook_reruns;
CREATE POLICY arsenal_title_hook_reruns_owner_read ON arsenal_title_hook_reruns FOR SELECT TO authenticated
    USING (public.is_owner());

GRANT SELECT ON arsenal_title_hooks, arsenal_title_hook_reruns TO valemont_readonly;

REVOKE ALL ON FUNCTION public.arsenal_title_hooks_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_title_hook_reruns_stamp() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.watch_status_title_hook() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.add_test_title_hook_cards(text, bigint, integer) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.add_test_title_hook_cards(text, bigint, integer) TO authenticated;
-- ctx_arsenal and ctx_screening keep their grants (CREATE OR REPLACE).

INSERT INTO migration_log (name) VALUES ('062_arsenal_title_hook')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 7
-- (Reserved: nothing.)

-- ===================================================================== 8
-- Self-test, run on paste, rolled back ('selftest-rollback'). Expected
-- refusals run in nested blocks and count only with the expected message.
-- The owner's session is simulated with request.jwt claims; the marker is a
-- throwaway e2e_markers row. Real-row checks use their own new rows (a real
-- title from tmdb_id 899000000 up, never a real TMDB id in practice, and
-- rolled back with everything else).

DO $$
DECLARE
    v_owner   uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_marker  text := 'selftest-062-' || md5(random()::text);
    v_real    bigint;
    v_test    bigint;
    v_ws      bigint;
    v_hook    bigint;
    v_thook   bigint;
    v_ref     bigint;
    v_ref2    bigint;
    v_jobs    int;
    v_n       int;
    v_ctx     jsonb;
BEGIN
    BEGIN
        IF v_owner IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: app_settings has no owner';
        END IF;
        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);
        INSERT INTO public.e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(v_marker, 'UTF8')), 'hex'));

        -- ------------------------------------------------- 1. a real title: watching drafts nothing; finished queues one
        INSERT INTO public.titles (tmdb_id, kind, name, overview)
        VALUES (899000000 + (floor(random() * 999999))::int, 'tv', 'Selftest 062 Show', 'A selftest show.')
        RETURNING id INTO v_real;
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_real, 'watching');
        IF EXISTS (SELECT 1 FROM public.arsenal_title_hooks WHERE title_id = v_real) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a move to watching wrote a hook';
        END IF;
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_real, 'finished') RETURNING id INTO v_ws;
        SELECT id INTO v_hook FROM public.arsenal_title_hooks
         WHERE title_id = v_real AND watch_status_id = v_ws AND status = 'queued' AND NOT is_test AND settled_at IS NULL;
        IF v_hook IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a move to finished wrote no queued hook';
        END IF;
        SELECT count(*) INTO v_jobs FROM public.job_queue
         WHERE kind = 'arsenal_title_hook' AND payload ->> 'hook_id' = v_hook::text;
        IF v_jobs <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % arsenal_title_hook jobs for one finish (want 1)', v_jobs;
        END IF;
        -- A re-watch: watching, then finished again. Nothing new.
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_real, 'watching');
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_real, 'finished');
        IF (SELECT count(*) FROM public.arsenal_title_hooks WHERE title_id = v_real) <> 1
           OR (SELECT count(*) FROM public.job_queue WHERE kind = 'arsenal_title_hook'
                 AND payload ->> 'title_id' = v_real::text) <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a re-watch drafted again';
        END IF;

        -- ------------------------------------------------- 2. cards: title_hook rules
        INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work,
                                         source_kind, source_page, status, origin_kind, source_title_id)
        VALUES ('selftest_062_card_a', 'film_tv', 'Selftest hook card', 'An origin long enough to pass.', 'A meaning.',
                'A line to deploy.', 'boardroom', 'Selftest 062 Show (2026)', 'wikipedia', 'Selftest', 'pool',
                'title_hook', v_real)
        RETURNING id INTO v_ref;
        v_n := 0;
        BEGIN  -- origin title_hook without a title
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work,
                                             source_kind, status, origin_kind)
            VALUES ('selftest_062_bad1', 'film_tv', 'Bad', 'An origin long enough to pass.', 'A meaning.', 'A line.',
                    'pitch', 'Work', 'wikipedia', 'pool', 'title_hook');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- a pool card naming a title
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work,
                                             source_kind, status, origin_kind, source_title_id)
            VALUES ('selftest_062_bad2', 'film_tv', 'Bad', 'An origin long enough to pass.', 'A meaning.', 'A line.',
                    'pitch', 'Work', 'wikipedia', 'pool', 'pool', v_real);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- a test card for a real title
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work,
                                             source_kind, status, origin_kind, source_title_id, is_test)
            VALUES ('e2e_selftest_062_bad3', 'film_tv', 'Bad', 'An origin long enough to pass.', 'A meaning.', 'A line.',
                    'pitch', 'Work', 'wikipedia', 'pool', 'title_hook', v_real, true);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%test exactly when its title is%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 3 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 3 title_hook card refusals', v_n; END IF;

        -- ------------------------------------------------- 3. the hook settles once
        v_n := 0;
        BEGIN
            UPDATE public.arsenal_title_hooks SET status = 'drafted' WHERE id = v_hook;  -- no cards
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN
            UPDATE public.arsenal_title_hooks SET title_id = title_id + 0, watch_status_id = v_ws + 1000000000 WHERE id = v_hook;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%identity never changes%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 2 early hook refusals', v_n; END IF;
        UPDATE public.arsenal_title_hooks SET status = 'drafted', ref_ids = ARRAY[v_ref], note = 'selftest' WHERE id = v_hook;
        IF (SELECT settled_at IS NULL OR status <> 'drafted' FROM public.arsenal_title_hooks WHERE id = v_hook) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the hook did not settle drafted';
        END IF;
        v_n := 0;
        BEGIN
            UPDATE public.arsenal_title_hooks SET note = 'again' WHERE id = v_hook;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%drafted hook never changes%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            DELETE FROM public.arsenal_title_hooks WHERE id = v_hook;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%never deleted%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN  -- no card after the draft
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work,
                                             source_kind, status, origin_kind, source_title_id)
            VALUES ('selftest_062_late', 'film_tv', 'Late', 'An origin long enough to pass.', 'A meaning.', 'A line.',
                    'pitch', 'Work', 'wikipedia', 'pool', 'title_hook', v_real);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%once per title%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN  -- a re-run of a drafted hook
            INSERT INTO public.arsenal_title_hook_reruns (hook_id, old_status, by_whom) VALUES (v_hook, 'failed', 'selftest');
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%only a failed hook%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 4 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 4 drafted-hook refusals', v_n; END IF;

        -- ------------------------------------------------- 4. failed, then a hand re-run
        INSERT INTO public.titles (tmdb_id, kind, name) VALUES (899000000 + (floor(random() * 999999))::int + 1000000, 'movie', 'Selftest 062 Film')
        RETURNING id INTO v_real;
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_real, 'finished');
        SELECT id INTO v_hook FROM public.arsenal_title_hooks WHERE title_id = v_real;
        UPDATE public.arsenal_title_hooks SET status = 'failed', note = 'selftest: no card passed' WHERE id = v_hook;
        v_n := 0;
        BEGIN
            UPDATE public.arsenal_title_hooks SET status = 'drafted' WHERE id = v_hook;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%hand re-run%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: a failed hook moved without a re-run'; END IF;
        INSERT INTO public.arsenal_title_hook_reruns (hook_id, old_status, by_whom) VALUES (v_hook, 'failed', 'selftest');
        INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work,
                                         source_kind, status, origin_kind, source_title_id)
        VALUES ('selftest_062_card_b', 'finance_business', 'Selftest rerun card', 'An origin long enough to pass.',
                'A meaning.', 'A line to deploy.', 'pitch', 'Selftest 062 Film (2026)', 'wikipedia', 'pool',
                'title_hook', v_real)
        RETURNING id INTO v_ref2;
        UPDATE public.arsenal_title_hooks SET status = 'drafted', ref_ids = ARRAY[v_ref2] WHERE id = v_hook;
        IF (SELECT status FROM public.arsenal_title_hooks WHERE id = v_hook) <> 'drafted'
           OR (SELECT role FROM public.arsenal_title_hook_reruns WHERE hook_id = v_hook) IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the hand re-run did not draft';
        END IF;

        -- ------------------------------------------------- 5. a test title: a hook, never a job; the e2e helper drafts it
        v_test := public.add_test_title(v_marker, 'tv', 'Selftest 062 test show');
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_test, 'finished');
        SELECT id INTO v_thook FROM public.arsenal_title_hooks WHERE title_id = v_test AND is_test AND status = 'queued';
        IF v_thook IS NULL OR EXISTS (SELECT 1 FROM public.job_queue WHERE kind = 'arsenal_title_hook'
                                        AND payload ->> 'title_id' = v_test::text) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test finish (hook %, or a job was queued)', v_thook;
        END IF;
        IF public.add_test_title_hook_cards(v_marker, v_test, 3) <> v_thook
           OR (SELECT status FROM public.arsenal_title_hooks WHERE id = v_thook) <> 'drafted'
           OR (SELECT count(*) FROM public.arsenal_refs WHERE source_title_id = v_test AND is_test AND origin_kind = 'title_hook') <> 3
           OR (SELECT count(*) FROM public.arsenal_refs WHERE source_title_id = v_test AND verification = 'unverified') <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: add_test_title_hook_cards';
        END IF;
        v_n := 0;
        BEGIN
            PERFORM public.add_test_title_hook_cards('not-the-marker', v_test, 3);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%verified test marker only%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            PERFORM public.add_test_title_hook_cards(v_marker, v_test, 3);  -- already drafted
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%queued hook%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 2 helper refusals', v_n; END IF;

        -- ------------------------------------------------- 6. context: real hooks listed, test ones never
        v_ctx := public.ctx_arsenal();
        IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_ctx -> 'recent_title_cards') x
                        WHERE x ->> 'title' = 'Selftest 062 Film' AND (x ->> 'cards')::int = 1 AND x ->> 'state' = 'drafted')
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_ctx -> 'recent_title_cards') x
                       WHERE x ->> 'title' LIKE 'Selftest 062 test%') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_arsenal recent_title_cards (got %)', v_ctx -> 'recent_title_cards';
        END IF;
        v_ctx := public.ctx_screening();
        IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_ctx -> 'finished_recent') x
                        WHERE x ->> 'title' = 'Selftest 062 Show' AND (x ->> 'arsenal_cards')::int = 1)
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_ctx -> 'finished_recent') x
                       WHERE x ->> 'title' LIKE 'Selftest 062 test%') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_screening finished_recent (got %)', v_ctx -> 'finished_recent';
        END IF;

        -- ------------------------------------------------- 7. TRUNCATE (every referencing table named)
        v_n := 0;
        BEGIN
            TRUNCATE public.arsenal_title_hooks, public.arsenal_title_hook_reruns;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%TRUNCATE is refused%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            TRUNCATE public.arsenal_title_hook_reruns;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%TRUNCATE is refused%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            UPDATE public.arsenal_title_hook_reruns SET by_whom = 'x' WHERE hook_id = v_hook;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%append-only%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 3 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 3 append-only refusals', v_n; END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '062 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '062_arsenal_title_hook';
-- (b) SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--      WHERE conrelid = 'public.arsenal_refs'::regclass
--        AND conname IN ('arsenal_refs_origin_kind_check', 'arsenal_refs_title_hook');
-- (c) SELECT c.relname, c.relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname),
--            has_table_privilege('anon', c.oid, 'SELECT'), has_table_privilege('authenticated', c.oid, 'INSERT,UPDATE,DELETE'),
--            has_table_privilege('valemont_readonly', c.oid, 'SELECT')
--       FROM pg_class c WHERE c.relname IN ('arsenal_title_hooks', 'arsenal_title_hook_reruns');
-- (d) SELECT tgname, tgrelid::regclass FROM pg_trigger WHERE tgname LIKE '%title_hook%' AND NOT tgisinternal ORDER BY 1;
-- (e) SELECT count(*) FROM arsenal_title_hooks; SELECT count(*) FROM arsenal_title_hook_reruns;  -- 0 and 0
-- (f) SELECT has_function_privilege('authenticated', 'public.add_test_title_hook_cards(text,bigint,integer)', 'EXECUTE'),
--            has_function_privilege('anon', 'public.add_test_title_hook_cards(text,bigint,integer)', 'EXECUTE');  -- t, f
