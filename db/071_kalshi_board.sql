-- 071_kalshi_board.sql — the Kalshi board (Chat 8 Phase 1; docs/kalshi_board.md).
--
-- Every open Kalshi sports market for the next ~36 h, every sport, game lines
-- and every prop: the PRE-GAME leg universe for Chat 8 Phases 2-4 (owner,
-- Nov 6). A CURRENT board of APP tables, upserted in place by the worker and
-- pruned; NOT the immutable kalshi_markets archive, which it never touches.
-- Read-only to Kalshi: no key, no order (CLAUDE.md §1).
--
-- What it adds:
--   1. kalshi_series_board: one row per series on the board: title, Kalshi's
--      sport tag, our league (NULL for most of Kalshi's sports), category
--      and tags, the CURRENT fee type and multiplier (from GET /series), the
--      regime as the worker priced it, combo eligibility (any event listed
--      by an open collection) and the collections, checked_at.
--   2. kalshi_board_events: one row per event: its game (the milestone's
--      main event ticker and title), milestone_start, starts_at (the linked
--      sports_events row's start when there is one, else the milestone's),
--      sports_event_id, close and expected expiration (the earliest of its
--      markets'), combo_eligible / combo_yes_only / combo_collections,
--      fetched_at (the last pull that asked for it).
--   3. kalshi_board: one row per market, narrow: kind (game_line | prop |
--      other), variant ('protected' for the *PROT series), subject, stat,
--      line, label (Kalshi's yes_sub_title), YES bid and ask, volume, open
--      interest, the taker fee per contract at the ask, fetched_at (the pull
--      whose values are stored), last_changed_at (the YES bid or ask last
--      moved). fillfactor 80 so the 15-min price updates stay HOT.
--      NO prices are the complement (no_bid = 1 - yes_ask, no_ask = 1 -
--      yes_bid; checked on live data before the paste: 0 mismatches) and
--      come from the view v_kalshi_board.
--   4. Test rows: a series ticker starting E2E is exactly a test row (CHECK);
--      events and markets carry is_test through composite foreign keys, so a
--      test market can never hang under a real event.
--   5. The worker's writers (SECURITY INVOKER; executable by no API role):
--      kalshi_board_series_upsert, _events_upsert, _markets_upsert (each
--      writes only a row whose stored columns would change, and returns the
--      count), kalshi_board_touch, kalshi_board_sweep (markets Kalshi no
--      longer lists), kalshi_board_link (sports_event_id and starts_at from
--      sports_events), kalshi_board_prune (an event and its markets once
--      coalesce(starts_at, close_time) + 6 h has passed; a series with no
--      event and unchecked for 7 days).
--   6. The page's readers (owner, RLS): kalshi_board_sports, kalshi_board_games
--      (one sport, games collapsed), kalshi_board_game (one game's markets,
--      on expand). Compact jsonb, nulls left out (egress).
--   7. ctx_kalshi_board(): provider `kalshi_board` (sort 48), NOT a brief
--      section (the brief line arrives with Phase 4).
--   8. add_test_kalshi_board: the owner and the verified e2e marker only.
--   9. Access: RLS on all three tables; the owner reads (SELECT only); the
--      worker writes through DATABASE_URL; anon nothing; valemont_readonly
--      reads.
--
-- A self-test runs on paste (section 11), rolled back, and raises on any
-- wrong result, so a bad paste applies nothing.

-- ===================================================================== 1
CREATE TABLE IF NOT EXISTS kalshi_series_board (
    series_ticker       TEXT         PRIMARY KEY CONSTRAINT kalshi_series_board_ticker_format
                                                 CHECK (series_ticker ~ '^[A-Z0-9][A-Z0-9._]{0,63}$'),
    title               TEXT         NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
    sport               TEXT         NOT NULL CHECK (char_length(sport) BETWEEN 1 AND 40),
    league              TEXT         REFERENCES sports_leagues (code),
    category            TEXT         CHECK (category IS NULL OR char_length(category) <= 60),
    tags                TEXT[]       NOT NULL DEFAULT '{}',
    fee_type            TEXT         NOT NULL CHECK (fee_type IN ('quadratic', 'quadratic_with_maker_fees',
                                                                  'quadratic_with_combo_maker_fees')),
    fee_multiplier      NUMERIC      NOT NULL CHECK (fee_multiplier > 0 AND fee_multiplier <= 10),
    fee_regime          JSONB        NOT NULL CHECK (jsonb_typeof(fee_regime) = 'object'),
    combo_eligible      BOOLEAN      NOT NULL DEFAULT false,
    collection_tickers  TEXT[]       NOT NULL DEFAULT '{}',
    is_test             BOOLEAN      NOT NULL DEFAULT false,
    checked_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT kalshi_series_board_test CHECK ((series_ticker LIKE 'E2E%') = is_test),
    CONSTRAINT kalshi_series_board_combo CHECK (combo_eligible = (cardinality(collection_tickers) > 0)),
    CONSTRAINT kalshi_series_board_ticker_test UNIQUE (series_ticker, is_test)
);

-- ===================================================================== 2
CREATE TABLE IF NOT EXISTS kalshi_board_events (
    event_ticker              TEXT         PRIMARY KEY CONSTRAINT kalshi_board_events_ticker_format
                                                       CHECK (event_ticker ~ '^[A-Z0-9][A-Z0-9._-]{1,100}$'),
    series_ticker             TEXT         NOT NULL,
    game_ticker               TEXT         NOT NULL CHECK (game_ticker ~ '^[A-Za-z0-9][A-Za-z0-9._-]{1,100}$'),
    game_title                TEXT         NOT NULL CHECK (char_length(game_title) BETWEEN 1 AND 200),
    milestone_start           TIMESTAMPTZ  NOT NULL,
    starts_at                 TIMESTAMPTZ  NOT NULL,
    sports_event_id           BIGINT       REFERENCES sports_events (id) ON DELETE SET NULL,
    close_time                TIMESTAMPTZ,
    expected_expiration_time  TIMESTAMPTZ,
    combo_eligible            BOOLEAN      NOT NULL DEFAULT false,
    combo_yes_only            BOOLEAN      NOT NULL DEFAULT false,
    combo_collections         TEXT[]       NOT NULL DEFAULT '{}',
    is_test                   BOOLEAN      NOT NULL DEFAULT false,
    fetched_at                TIMESTAMPTZ  NOT NULL,
    created_at                TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT kalshi_board_events_series FOREIGN KEY (series_ticker, is_test)
        REFERENCES kalshi_series_board (series_ticker, is_test),
    CONSTRAINT kalshi_board_events_under_series CHECK (event_ticker LIKE series_ticker || '-%'),
    CONSTRAINT kalshi_board_events_combo CHECK (combo_eligible = (cardinality(combo_collections) > 0)
                                                AND (combo_eligible OR NOT combo_yes_only)),
    CONSTRAINT kalshi_board_events_ticker_test UNIQUE (event_ticker, is_test)
);

CREATE INDEX IF NOT EXISTS kalshi_board_events_starts ON kalshi_board_events (starts_at);
CREATE INDEX IF NOT EXISTS kalshi_board_events_game ON kalshi_board_events (game_ticker);
CREATE INDEX IF NOT EXISTS kalshi_board_events_series_idx ON kalshi_board_events (series_ticker);
CREATE INDEX IF NOT EXISTS kalshi_board_events_sports_event ON kalshi_board_events (sports_event_id)
    WHERE sports_event_id IS NOT NULL;

-- ===================================================================== 3
CREATE TABLE IF NOT EXISTS kalshi_board (
    market_ticker      TEXT          PRIMARY KEY CONSTRAINT kalshi_board_ticker_format
                                                 CHECK (market_ticker ~ '^[A-Z0-9][A-Za-z0-9._-]{1,160}$'),
    event_ticker       TEXT          NOT NULL,
    market_kind        TEXT          NOT NULL CHECK (market_kind IN ('game_line', 'prop', 'other')),
    variant            TEXT          CHECK (variant IS NULL OR variant IN ('protected')),
    subject            TEXT          CHECK (subject IS NULL OR char_length(subject) BETWEEN 1 AND 120),
    stat               TEXT          CHECK (stat IS NULL OR char_length(stat) BETWEEN 1 AND 60),
    line               NUMERIC,
    label              TEXT          CHECK (label IS NULL OR char_length(label) BETWEEN 1 AND 160),
    yes_bid            NUMERIC(5, 4) CHECK (yes_bid IS NULL OR yes_bid BETWEEN 0 AND 1),
    yes_ask            NUMERIC(5, 4) CHECK (yes_ask IS NULL OR yes_ask BETWEEN 0 AND 1),
    volume             NUMERIC       CHECK (volume IS NULL OR volume >= 0),
    open_interest      NUMERIC       CHECK (open_interest IS NULL OR open_interest >= 0),
    taker_fee_at_ask   NUMERIC(8, 6) CHECK (taker_fee_at_ask IS NULL OR taker_fee_at_ask BETWEEN 0 AND 1),
    is_test            BOOLEAN       NOT NULL DEFAULT false,
    fetched_at         TIMESTAMPTZ   NOT NULL,
    last_changed_at    TIMESTAMPTZ   NOT NULL,
    CONSTRAINT kalshi_board_event FOREIGN KEY (event_ticker, is_test)
        REFERENCES kalshi_board_events (event_ticker, is_test) ON DELETE CASCADE,
    CONSTRAINT kalshi_board_under_event CHECK (market_ticker LIKE event_ticker || '-%')
) WITH (fillfactor = 80);

CREATE INDEX IF NOT EXISTS kalshi_board_event_idx ON kalshi_board (event_ticker);

COMMENT ON TABLE kalshi_board IS
    'The current pre-game board of open Kalshi sports markets (docs/kalshi_board.md). Worker upserts in place '
    'and prunes; not the kalshi_markets archive. NO prices: v_kalshi_board.';

-- ===================================================================== 4
-- NO prices as the complement of YES (a binary book; 0 mismatches over the
-- live board, checked before the paste).
CREATE OR REPLACE VIEW v_kalshi_board WITH (security_invoker = true) AS
SELECT b.market_ticker, b.event_ticker, e.series_ticker, e.game_ticker, e.game_title, e.starts_at,
       e.sports_event_id, s.sport, s.league, b.market_kind, b.variant, b.subject, b.stat, b.line, b.label,
       b.yes_bid, b.yes_ask,
       CASE WHEN b.yes_ask IS NULL THEN NULL ELSE 1 - b.yes_ask END AS no_bid,
       CASE WHEN b.yes_bid IS NULL THEN NULL ELSE 1 - b.yes_bid END AS no_ask,
       b.volume, b.open_interest, b.taker_fee_at_ask, s.fee_type, s.fee_multiplier,
       e.combo_eligible, e.combo_yes_only, e.combo_collections, e.close_time, e.expected_expiration_time,
       b.fetched_at, b.last_changed_at, e.fetched_at AS event_fetched_at, b.is_test
  FROM kalshi_board b
  JOIN kalshi_board_events e ON e.event_ticker = b.event_ticker
  JOIN kalshi_series_board s ON s.series_ticker = e.series_ticker;

-- ===================================================================== 5
-- The worker's writers. Each writes only a row whose stored columns would
-- change and returns how many it wrote; is_test selects which rows it may
-- touch (the worker passes false, tests_live true).

CREATE OR REPLACE FUNCTION public.kalshi_board_series_upsert(p_rows jsonb, p_is_test boolean DEFAULT false)
RETURNS integer
LANGUAGE sql
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH w AS (
        INSERT INTO public.kalshi_series_board AS s
               (series_ticker, title, sport, league, category, tags, fee_type, fee_multiplier, fee_regime,
                combo_eligible, collection_tickers, is_test, checked_at)
        SELECT r.series_ticker, r.title, r.sport, r.league, r.category, coalesce(r.tags, '{}'), r.fee_type,
               r.fee_multiplier, r.fee_regime, coalesce(r.combo_eligible, false),
               coalesce(r.collection_tickers, '{}'), p_is_test, now()
          FROM jsonb_to_recordset(p_rows) AS r (
                   series_ticker text, title text, sport text, league text, category text, tags text[],
                   fee_type text, fee_multiplier numeric, fee_regime jsonb, combo_eligible boolean,
                   collection_tickers text[])
        ON CONFLICT (series_ticker) DO UPDATE SET
            title = EXCLUDED.title, sport = EXCLUDED.sport, league = EXCLUDED.league,
            category = EXCLUDED.category, tags = EXCLUDED.tags, fee_type = EXCLUDED.fee_type,
            fee_multiplier = EXCLUDED.fee_multiplier, fee_regime = EXCLUDED.fee_regime,
            combo_eligible = EXCLUDED.combo_eligible, collection_tickers = EXCLUDED.collection_tickers,
            checked_at = now()
         WHERE s.is_test = p_is_test
           AND (s.title, s.sport, s.league, s.category, s.tags, s.fee_type, s.fee_multiplier, s.fee_regime,
                s.combo_eligible, s.collection_tickers)
               IS DISTINCT FROM
               (EXCLUDED.title, EXCLUDED.sport, EXCLUDED.league, EXCLUDED.category, EXCLUDED.tags,
                EXCLUDED.fee_type, EXCLUDED.fee_multiplier, EXCLUDED.fee_regime, EXCLUDED.combo_eligible,
                EXCLUDED.collection_tickers)
        RETURNING 1
    )
    SELECT count(*)::integer FROM w;
$$;

CREATE OR REPLACE FUNCTION public.kalshi_board_events_upsert(p_rows jsonb, p_at timestamptz,
                                                             p_is_test boolean DEFAULT false)
RETURNS integer
LANGUAGE sql
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH w AS (
        INSERT INTO public.kalshi_board_events AS e
               (event_ticker, series_ticker, game_ticker, game_title, milestone_start, starts_at, close_time,
                expected_expiration_time, combo_eligible, combo_yes_only, combo_collections, is_test, fetched_at)
        SELECT r.event_ticker, r.series_ticker, r.game_ticker, r.game_title, r.milestone_start,
               r.milestone_start, r.close_time, r.expected_expiration_time, coalesce(r.combo_eligible, false),
               coalesce(r.combo_yes_only, false), coalesce(r.combo_collections, '{}'), p_is_test, p_at
          FROM jsonb_to_recordset(p_rows) AS r (
                   event_ticker text, series_ticker text, game_ticker text, game_title text,
                   milestone_start timestamptz, close_time timestamptz, expected_expiration_time timestamptz,
                   combo_eligible boolean, combo_yes_only boolean, combo_collections text[])
        ON CONFLICT (event_ticker) DO UPDATE SET
            series_ticker = EXCLUDED.series_ticker, game_ticker = EXCLUDED.game_ticker,
            game_title = EXCLUDED.game_title, milestone_start = EXCLUDED.milestone_start,
            -- A linked event keeps ESPN's start (kalshi_board_link); else the milestone's.
            starts_at = CASE WHEN e.sports_event_id IS NULL THEN EXCLUDED.milestone_start ELSE e.starts_at END,
            close_time = EXCLUDED.close_time, expected_expiration_time = EXCLUDED.expected_expiration_time,
            combo_eligible = EXCLUDED.combo_eligible, combo_yes_only = EXCLUDED.combo_yes_only,
            combo_collections = EXCLUDED.combo_collections, fetched_at = greatest(e.fetched_at, EXCLUDED.fetched_at)
         WHERE e.is_test = p_is_test
           AND (e.series_ticker, e.game_ticker, e.game_title, e.milestone_start, e.close_time,
                e.expected_expiration_time, e.combo_eligible, e.combo_yes_only, e.combo_collections)
               IS DISTINCT FROM
               (EXCLUDED.series_ticker, EXCLUDED.game_ticker, EXCLUDED.game_title, EXCLUDED.milestone_start,
                EXCLUDED.close_time, EXCLUDED.expected_expiration_time, EXCLUDED.combo_eligible,
                EXCLUDED.combo_yes_only, EXCLUDED.combo_collections)
        RETURNING 1
    )
    SELECT count(*)::integer FROM w;
$$;

CREATE OR REPLACE FUNCTION public.kalshi_board_markets_upsert(p_rows jsonb, p_at timestamptz,
                                                              p_is_test boolean DEFAULT false)
RETURNS integer
LANGUAGE sql
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH w AS (
        INSERT INTO public.kalshi_board AS b
               (market_ticker, event_ticker, market_kind, variant, subject, stat, line, label, yes_bid, yes_ask,
                volume, open_interest, taker_fee_at_ask, is_test, fetched_at, last_changed_at)
        SELECT r.market_ticker, r.event_ticker, r.market_kind, r.variant, r.subject, r.stat, r.line, r.label,
               r.yes_bid, r.yes_ask, r.volume, r.open_interest, r.taker_fee_at_ask, p_is_test, p_at, p_at
          FROM jsonb_to_recordset(p_rows) AS r (
                   market_ticker text, event_ticker text, market_kind text, variant text, subject text, stat text,
                   line numeric, label text, yes_bid numeric, yes_ask numeric, volume numeric,
                   open_interest numeric, taker_fee_at_ask numeric)
        ON CONFLICT (market_ticker) DO UPDATE SET
            event_ticker = EXCLUDED.event_ticker, market_kind = EXCLUDED.market_kind, variant = EXCLUDED.variant,
            subject = EXCLUDED.subject, stat = EXCLUDED.stat, line = EXCLUDED.line, label = EXCLUDED.label,
            yes_bid = EXCLUDED.yes_bid, yes_ask = EXCLUDED.yes_ask, volume = EXCLUDED.volume,
            open_interest = EXCLUDED.open_interest, taker_fee_at_ask = EXCLUDED.taker_fee_at_ask,
            fetched_at = greatest(b.fetched_at, EXCLUDED.fetched_at),
            last_changed_at = CASE WHEN (b.yes_bid, b.yes_ask) IS DISTINCT FROM (EXCLUDED.yes_bid, EXCLUDED.yes_ask)
                                   THEN greatest(b.last_changed_at, EXCLUDED.fetched_at)
                                   ELSE b.last_changed_at END
         WHERE b.is_test = p_is_test
           AND (b.event_ticker, b.market_kind, b.variant, b.subject, b.stat, b.line, b.label, b.yes_bid, b.yes_ask,
                b.volume, b.open_interest, b.taker_fee_at_ask)
               IS DISTINCT FROM
               (EXCLUDED.event_ticker, EXCLUDED.market_kind, EXCLUDED.variant, EXCLUDED.subject, EXCLUDED.stat,
                EXCLUDED.line, EXCLUDED.label, EXCLUDED.yes_bid, EXCLUDED.yes_ask, EXCLUDED.volume,
                EXCLUDED.open_interest, EXCLUDED.taker_fee_at_ask)
        RETURNING 1
    )
    SELECT count(*)::integer FROM w;
$$;

-- The pull asked for these events and series at p_at (whatever changed).
CREATE OR REPLACE FUNCTION public.kalshi_board_touch(p_events text[], p_series text[], p_at timestamptz,
                                                     p_is_test boolean DEFAULT false)
RETURNS void
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
BEGIN
    UPDATE public.kalshi_board_events SET fetched_at = p_at
     WHERE is_test = p_is_test AND event_ticker = ANY(coalesce(p_events, '{}')) AND fetched_at < p_at;
    UPDATE public.kalshi_series_board SET checked_at = p_at
     WHERE is_test = p_is_test AND series_ticker = ANY(coalesce(p_series, '{}')) AND checked_at < p_at;
END;
$$;

-- Markets of the events asked for that Kalshi no longer lists as open.
CREATE OR REPLACE FUNCTION public.kalshi_board_sweep(p_events text[], p_markets text[],
                                                     p_is_test boolean DEFAULT false)
RETURNS integer
LANGUAGE sql
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH d AS (
        DELETE FROM public.kalshi_board
         WHERE is_test = p_is_test AND event_ticker = ANY(coalesce(p_events, '{}'))
           AND NOT (market_ticker = ANY(coalesce(p_markets, '{}')))
        RETURNING 1
    )
    SELECT count(*)::integer FROM d;
$$;

-- sports_event_id and starts_at from sports_events (kalshi_map's links): an
-- event's game ticker is the linked row's kalshi_event_ticker; of several
-- (golf rounds, F1 sessions share one) the one starting nearest the
-- milestone. Unlinked: the milestone's start.
CREATE OR REPLACE FUNCTION public.kalshi_board_link(p_is_test boolean DEFAULT false)
RETURNS integer
LANGUAGE sql
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH cand AS (
        SELECT DISTINCT ON (e.event_ticker) e.event_ticker, s.id, s.starts_at
          FROM public.kalshi_board_events e
          JOIN public.sports_events s ON s.kalshi_event_ticker = e.game_ticker AND s.is_test = e.is_test
         WHERE e.is_test = p_is_test
         ORDER BY e.event_ticker, abs(extract(epoch FROM s.starts_at - e.milestone_start)), s.id
    ),
    want AS (
        SELECT e.event_ticker, c.id AS sid, coalesce(c.starts_at, e.milestone_start) AS starts
          FROM public.kalshi_board_events e
          LEFT JOIN cand c ON c.event_ticker = e.event_ticker
         WHERE e.is_test = p_is_test
    ),
    u AS (
        UPDATE public.kalshi_board_events e SET sports_event_id = w.sid, starts_at = w.starts
          FROM want w
         WHERE e.event_ticker = w.event_ticker
           AND (e.sports_event_id, e.starts_at) IS DISTINCT FROM (w.sid, w.starts)
        RETURNING 1
    )
    SELECT count(*)::integer FROM u;
$$;

-- The pre-game rule (owner, Nov 6): an event and its markets go once
-- coalesce(starts_at, close_time) + 6 h has passed; a series with no event
-- left and not checked for 7 days goes too.
CREATE OR REPLACE FUNCTION public.kalshi_board_prune(p_is_test boolean DEFAULT false)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
    v_markets int;
    v_events  int;
    v_series  int;
BEGIN
    DELETE FROM public.kalshi_board b
     USING public.kalshi_board_events e
     WHERE b.event_ticker = e.event_ticker AND e.is_test = p_is_test
       AND coalesce(e.starts_at, e.close_time) + interval '6 hours' < now();
    GET DIAGNOSTICS v_markets = ROW_COUNT;
    DELETE FROM public.kalshi_board_events e
     WHERE e.is_test = p_is_test AND coalesce(e.starts_at, e.close_time) + interval '6 hours' < now();
    GET DIAGNOSTICS v_events = ROW_COUNT;
    DELETE FROM public.kalshi_series_board s
     WHERE s.is_test = p_is_test AND s.checked_at < now() - interval '7 days'
       AND NOT EXISTS (SELECT 1 FROM public.kalshi_board_events e WHERE e.series_ticker = s.series_ticker);
    GET DIAGNOSTICS v_series = ROW_COUNT;
    RETURN jsonb_build_object('events', v_events, 'markets', v_markets, 'series', v_series);
END;
$$;

-- ===================================================================== 6
-- The page's readers (the owner, through RLS). Compact jsonb, nulls left
-- out. p_tests shows test rows too (a marked e2e request only).

-- Sport chips: one object per sport with an upcoming market.
CREATE OR REPLACE FUNCTION public.kalshi_board_sports(p_tests boolean DEFAULT false)
RETURNS SETOF jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT jsonb_strip_nulls(jsonb_build_object(
               'sport', s.sport, 'games', count(DISTINCT e.game_ticker), 'markets', count(*),
               'lines', count(*) FILTER (WHERE b.market_kind = 'game_line'),
               'props', count(*) FILTER (WHERE b.market_kind = 'prop'),
               'combo', count(*) FILTER (WHERE e.combo_eligible)))
      FROM public.kalshi_board b
      JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
      JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
     WHERE (NOT b.is_test OR p_tests) AND e.starts_at > now() - interval '6 hours'
     GROUP BY s.sport
     ORDER BY count(*) DESC, s.sport;
$$;

-- One sport's games, collapsed: counts per kind, the start, the league.
CREATE OR REPLACE FUNCTION public.kalshi_board_games(p_sport text, p_kind text DEFAULT NULL,
                                                     p_combo_only boolean DEFAULT false,
                                                     p_tests boolean DEFAULT false)
RETURNS SETOF jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT jsonb_strip_nulls(jsonb_build_object(
               'g', e.game_ticker, 't', min(e.game_title), 'at', min(e.starts_at), 'lg', min(s.league),
               'se', min(e.sports_event_id),
               'l', nullif(count(*) FILTER (WHERE b.market_kind = 'game_line'), 0),
               'p', nullif(count(*) FILTER (WHERE b.market_kind = 'prop'), 0),
               'o', nullif(count(*) FILTER (WHERE b.market_kind = 'other'), 0),
               'c', nullif(count(*) FILTER (WHERE e.combo_eligible), 0),
               'asof', max(e.fetched_at),
               'test', CASE WHEN bool_or(b.is_test) THEN true END))
      FROM public.kalshi_board b
      JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
      JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
     WHERE (NOT b.is_test OR p_tests) AND s.sport = p_sport AND e.starts_at > now() - interval '6 hours'
       AND (p_kind IS NULL OR b.market_kind = p_kind) AND (NOT p_combo_only OR e.combo_eligible)
     GROUP BY e.game_ticker
     ORDER BY min(e.starts_at), min(e.game_title);
$$;

-- One game's markets (on expand), one object per event: series title, combo
-- flags, fetched_at, and its markets as compact objects (kind, label, YES
-- bid/ask, fee at the ask, variant): game lines first, then props, then other.
CREATE OR REPLACE FUNCTION public.kalshi_board_game(p_game text, p_kind text DEFAULT NULL,
                                                    p_combo_only boolean DEFAULT false,
                                                    p_tests boolean DEFAULT false)
RETURNS SETOF jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH m AS (
        SELECT e.event_ticker, s.title AS series_title, e.combo_eligible, e.combo_yes_only, e.fetched_at,
               b.market_kind, b.variant, b.label, b.subject, b.line, b.yes_bid, b.yes_ask, b.taker_fee_at_ask,
               CASE b.market_kind WHEN 'game_line' THEN 0 WHEN 'prop' THEN 1 ELSE 2 END AS k_order
          FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
          JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
         WHERE e.game_ticker = p_game AND (NOT b.is_test OR p_tests)
           AND (p_kind IS NULL OR b.market_kind = p_kind) AND (NOT p_combo_only OR e.combo_eligible)
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
               'e', m.event_ticker, 'sr', min(m.series_title),
               'c', CASE WHEN bool_or(m.combo_eligible) THEN true END,
               'y', CASE WHEN bool_or(m.combo_yes_only) THEN true END,
               'asof', max(m.fetched_at),
               'm', jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                        'k', m.market_kind, 'lb', m.label, 'b', m.yes_bid, 'a', m.yes_ask,
                        'f', m.taker_fee_at_ask, 'v', m.variant))
                    ORDER BY m.k_order, m.subject NULLS LAST, m.line NULLS FIRST, m.label)))
      FROM m
     GROUP BY m.event_ticker
     ORDER BY min(m.k_order), min(m.series_title), m.event_ticker;
$$;

-- ===================================================================== 7
CREATE OR REPLACE FUNCTION public.ctx_kalshi_board()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH z AS (SELECT coalesce((SELECT timezone FROM public.app_settings LIMIT 1), 'America/Chicago') AS tz),
    b AS (
        SELECT s.sport, b.market_kind, b.label, b.yes_bid, b.yes_ask, b.volume, e.combo_eligible,
               e.game_title, e.starts_at, e.event_ticker
          FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
          JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
         WHERE NOT b.is_test AND e.starts_at > now()
    )
    SELECT jsonb_build_object(
        'about', 'The Kalshi board: every open Kalshi sports market for the next ~36 h (game lines, props, '
                 'other), from Kalshi''s public data, refreshed through the day. Prices are YES in dollars '
                 '(= probability). A board, not advice: nothing here is a pick, and nothing is ever bet '
                 'automatically.',
        'as_of', (SELECT max(fetched_at) FROM public.kalshi_board_events WHERE NOT is_test),
        'counts', coalesce((SELECT jsonb_object_agg(sport, kinds) FROM (
                      SELECT sport, jsonb_object_agg(market_kind, n) AS kinds
                        FROM (SELECT sport, market_kind, count(*) AS n FROM b GROUP BY sport, market_kind) k
                       GROUP BY sport) x), '{}'::jsonb),
        'combo_eligible', jsonb_build_object(
            'markets', (SELECT count(*) FROM b WHERE combo_eligible),
            'events', (SELECT count(DISTINCT event_ticker) FROM b WHERE combo_eligible)),
        'top_tonight', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                                    'market', public.ctx_trim(t.label, 100), 'game', public.ctx_trim(t.game_title, 100),
                                    'sport', t.sport, 'kind', t.market_kind,
                                    'at', to_char(t.starts_at AT TIME ZONE z.tz, 'HH24:MI'),
                                    'yes_bid', t.yes_bid, 'yes_ask', t.yes_ask, 'volume', t.volume))
                                    ORDER BY t.volume DESC NULLS LAST)
                                  FROM (SELECT * FROM b WHERE starts_at < now() + interval '12 hours'
                                         ORDER BY volume DESC NULLS LAST LIMIT 10) t CROSS JOIN z), '[]'::jsonb)
    );
$$;

INSERT INTO context_providers (key, label, fn, brief_section, sort)
VALUES ('kalshi_board', 'Kalshi board', 'public.ctx_kalshi_board()'::regprocedure, false, 48)
ON CONFLICT (key) DO NOTHING;

-- ===================================================================== 8
-- Test rows (e2e): the owner and the verified e2e marker only. One test game
-- (series E2ETEST, sport 'Basketball') starting at p_starts_at: a moneyline
-- event (2 markets, combo-eligible, YES only) and a points event (3 props).
-- Returns the game ticker.
CREATE OR REPLACE FUNCTION public.add_test_kalshi_board(p_marker text, p_starts_at timestamptz)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_tag  text := upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 8));
    v_game text;
    v_pts  text;
BEGIN
    IF NOT coalesce(public.e2e_marker_ok(p_marker), false) THEN
        RAISE EXCEPTION 'add_test_kalshi_board: the owner and a verified test marker only';
    END IF;
    v_game := 'E2ETEST-' || v_tag;
    v_pts := 'E2ETEST-' || v_tag || 'PTS';
    INSERT INTO public.kalshi_series_board (series_ticker, title, sport, tags, fee_type, fee_multiplier, fee_regime,
                                            combo_eligible, collection_tickers, is_test)
    VALUES ('E2ETEST', 'E2E Test Series', 'Basketball', ARRAY['Basketball'], 'quadratic', 1,
            '{"fee_type": "quadratic", "fee_multiplier": "1"}'::jsonb, true, ARRAY['E2ECOLL-R'], true)
    ON CONFLICT (series_ticker) DO NOTHING;
    INSERT INTO public.kalshi_board_events (event_ticker, series_ticker, game_ticker, game_title, milestone_start,
                                            starts_at, combo_eligible, combo_yes_only, combo_collections,
                                            is_test, fetched_at)
    VALUES (v_game, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, true, true,
            ARRAY['E2ECOLL-R'], true, now()),
           (v_pts, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, false, false,
            '{}', true, now());
    INSERT INTO public.kalshi_board (market_ticker, event_ticker, market_kind, variant, subject, stat, line, label,
                                     yes_bid, yes_ask, volume, open_interest, taker_fee_at_ask, is_test,
                                     fetched_at, last_changed_at)
    SELECT v.market_ticker, v.event_ticker, v.market_kind, v.variant, v.subject, v.stat, v.line, v.label,
           v.yes_bid, v.yes_ask, v.volume, v.open_interest, v.fee, true, now(), now()
      FROM (VALUES
        (v_game || '-HOM', v_game, 'game_line', NULL::text, 'Test Home', 'winner', NULL::numeric, 'Test Home',
         0.6100::numeric, 0.6300::numeric, 1200::numeric, 800::numeric, 0.016317::numeric),
        (v_game || '-AWY', v_game, 'game_line', NULL, 'Test Away', 'winner', NULL, 'Test Away',
         0.3700, 0.3900, 900, 700, 0.016653),
        (v_pts || '-P1-10', v_pts, 'prop', NULL, 'Test Player', 'points', 9.5, 'Test Player: 10+',
         0.5500, 0.5800, 300, 200, 0.017052),
        (v_pts || '-P1-20', v_pts, 'prop', NULL, 'Test Player', 'points', 19.5, 'Test Player: 20+',
         0.1500, 0.1700, 150, 100, 0.009877),
        (v_pts || '-P1-10P', v_pts, 'prop', 'protected', 'Test Player', 'points', 9.5, 'Test Player: 10+',
         0.5400, 0.5700, 50, 40, 0.017157)
      ) AS v (market_ticker, event_ticker, market_kind, variant, subject, stat, line, label, yes_bid, yes_ask,
              volume, open_interest, fee);
    RETURN v_game;
END;
$$;

-- ===================================================================== 9
-- Access.
ALTER TABLE kalshi_series_board ENABLE ROW LEVEL SECURITY;
ALTER TABLE kalshi_board_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE kalshi_board ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON kalshi_series_board, kalshi_board_events, kalshi_board FROM anon, authenticated, PUBLIC;
REVOKE ALL ON v_kalshi_board FROM anon, authenticated, PUBLIC;
GRANT SELECT ON kalshi_series_board, kalshi_board_events, kalshi_board, v_kalshi_board TO authenticated;
GRANT SELECT ON kalshi_series_board, kalshi_board_events, kalshi_board, v_kalshi_board TO valemont_readonly;
DROP POLICY IF EXISTS kalshi_series_board_owner_read ON kalshi_series_board;
CREATE POLICY kalshi_series_board_owner_read ON kalshi_series_board FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS kalshi_board_events_owner_read ON kalshi_board_events;
CREATE POLICY kalshi_board_events_owner_read ON kalshi_board_events FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS kalshi_board_owner_read ON kalshi_board;
CREATE POLICY kalshi_board_owner_read ON kalshi_board FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));

REVOKE ALL ON FUNCTION public.kalshi_board_series_upsert(jsonb, boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.kalshi_board_events_upsert(jsonb, timestamptz, boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.kalshi_board_markets_upsert(jsonb, timestamptz, boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.kalshi_board_touch(text[], text[], timestamptz, boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.kalshi_board_sweep(text[], text[], boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.kalshi_board_link(boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.kalshi_board_prune(boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.kalshi_board_sports(boolean) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.kalshi_board_games(text, text, boolean, boolean) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.kalshi_board_game(text, text, boolean, boolean) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.ctx_kalshi_board() FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.add_test_kalshi_board(text, timestamptz) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.kalshi_board_sports(boolean) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.kalshi_board_games(text, text, boolean, boolean) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.kalshi_board_game(text, text, boolean, boolean) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.ctx_kalshi_board() TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.add_test_kalshi_board(text, timestamptz) TO authenticated;

-- ===================================================================== 10
INSERT INTO migration_log (name) VALUES ('071_kalshi_board')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 11
-- Self-test, run on paste, rolled back ('selftest-rollback'). Each expected
-- refusal runs in its own nested block and counts only with its SQLSTATE.
-- Real rows use series KXSELFTEST071 (no real series has it); a sports_events
-- row id from the sequence, linked to the self-test game. Every statement
-- that calls a writing function stores its result first and reads back in a
-- later statement (db/062's rule); every INSERT tuple was checked against its
-- column list before the paste (db/054's rule).

DO $$
DECLARE
    v_owner   uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_marker  text := 'selftest-071-' || gen_random_uuid();
    v_t0      timestamptz := now() - interval '1 minute';
    v_t1      timestamptz := now();
    v_soon    timestamptz := now() + interval '2 hours';
    v_old     timestamptz := now() - interval '7 hours';
    v_ok      boolean;
    v_n       int;
    v_j       jsonb;
    v_sid     bigint;
    v_game    text;
    v_row     record;
    v_series  jsonb := '[{"series_ticker": "KXSELFTEST071", "title": "Selftest Series", "sport": "Basketball",
                          "league": "nba", "category": "Sports", "tags": ["Basketball"], "fee_type": "quadratic",
                          "fee_multiplier": "1", "fee_regime": {"fee_type": "quadratic"}, "combo_eligible": true,
                          "collection_tickers": ["KXMVESELFTEST-R"]}]';
    v_events  jsonb;
    v_mk      jsonb;
BEGIN
    BEGIN
        IF v_owner IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: app_settings has no owner';
        END IF;
        v_events := jsonb_build_array(
            jsonb_build_object('event_ticker', 'KXSELFTEST071-26NOV07AAABBB', 'series_ticker', 'KXSELFTEST071',
                               'game_ticker', 'KXSELFTEST071GAME-26NOV07AAABBB', 'game_title', 'AAA vs BBB',
                               'milestone_start', v_soon, 'close_time', v_soon + interval '2 days',
                               'combo_eligible', true, 'combo_yes_only', false,
                               'combo_collections', jsonb_build_array('KXMVESELFTEST-R')),
            jsonb_build_object('event_ticker', 'KXSELFTEST071-26NOV06CCCDDD', 'series_ticker', 'KXSELFTEST071',
                               'game_ticker', 'KXSELFTEST071GAME-26NOV06CCCDDD', 'game_title', 'CCC vs DDD',
                               'milestone_start', v_old, 'close_time', v_old + interval '2 days',
                               'combo_eligible', false, 'combo_yes_only', false,
                               'combo_collections', '[]'::jsonb));
        v_mk := jsonb_build_array(
            jsonb_build_object('market_ticker', 'KXSELFTEST071-26NOV07AAABBB-P1-10', 'event_ticker',
                               'KXSELFTEST071-26NOV07AAABBB', 'market_kind', 'prop', 'subject', 'Player One',
                               'stat', 'rebounds', 'line', 9.5, 'label', 'Player One: 10+', 'yes_bid', 0.40,
                               'yes_ask', 0.42, 'volume', 100, 'open_interest', 50, 'taker_fee_at_ask', 0.017052),
            jsonb_build_object('market_ticker', 'KXSELFTEST071-26NOV07AAABBB-P1-12', 'event_ticker',
                               'KXSELFTEST071-26NOV07AAABBB', 'market_kind', 'prop', 'subject', 'Player One',
                               'stat', 'rebounds', 'line', 11.5, 'label', 'Player One: 12+', 'yes_bid', 0.20,
                               'yes_ask', 0.22, 'volume', 900, 'open_interest', 50, 'taker_fee_at_ask', 0.012012),
            jsonb_build_object('market_ticker', 'KXSELFTEST071-26NOV06CCCDDD-P2-5', 'event_ticker',
                               'KXSELFTEST071-26NOV06CCCDDD', 'market_kind', 'prop', 'subject', 'Player Two',
                               'stat', 'rebounds', 'line', 4.5, 'label', 'Player Two: 5+', 'yes_bid', 0.70,
                               'yes_ask', 0.72, 'volume', 10, 'open_interest', 5, 'taker_fee_at_ask', 0.014112));

        -- ------------------------------------------------ 1. series upsert
        v_n := public.kalshi_board_series_upsert(v_series);
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: series insert wrote %', v_n; END IF;
        v_n := public.kalshi_board_series_upsert(v_series);
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: an unchanged series was rewritten (%)', v_n; END IF;
        v_n := public.kalshi_board_series_upsert(jsonb_set(v_series, '{0,fee_type}', '"quadratic_with_maker_fees"'));
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: a fee change was not written (%)', v_n; END IF;

        -- ------------------------------------------------ 2. events and markets
        v_n := public.kalshi_board_events_upsert(v_events, v_t0);
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: events insert wrote %', v_n; END IF;
        v_n := public.kalshi_board_events_upsert(v_events, v_t1);
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: unchanged events were rewritten (%)', v_n; END IF;
        v_n := public.kalshi_board_markets_upsert(v_mk, v_t0);
        IF v_n <> 3 THEN RAISE EXCEPTION 'SELFTEST FAILED: markets insert wrote %', v_n; END IF;
        v_n := public.kalshi_board_markets_upsert(v_mk, v_t1);
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: unchanged markets were rewritten (%)', v_n; END IF;
        v_n := public.kalshi_board_markets_upsert(jsonb_set(v_mk, '{0,yes_ask}', '0.45'), v_t1);
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: a price change wrote % rows', v_n; END IF;
        SELECT fetched_at, last_changed_at, yes_ask INTO v_row
          FROM public.kalshi_board WHERE market_ticker = 'KXSELFTEST071-26NOV07AAABBB-P1-10';
        IF v_row.last_changed_at <> v_t1 OR v_row.fetched_at <> v_t1 OR v_row.yes_ask <> 0.45 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: after a price change the row reads %', v_row;
        END IF;
        -- The volume moves, the prices stay (0.45 as just written).
        v_n := public.kalshi_board_markets_upsert(
                   jsonb_set(jsonb_set(v_mk, '{0,yes_ask}', '0.45'), '{0,volume}', '101'), v_t1 + interval '1 minute');
        SELECT fetched_at, last_changed_at INTO v_row
          FROM public.kalshi_board WHERE market_ticker = 'KXSELFTEST071-26NOV07AAABBB-P1-10';
        IF v_n <> 1 OR v_row.last_changed_at <> v_t1 OR v_row.fetched_at <> v_t1 + interval '1 minute' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a volume-only change moved last_changed_at (% / %)', v_n, v_row;
        END IF;

        -- The view's NO side is the complement.
        SELECT no_bid, no_ask INTO v_row FROM public.v_kalshi_board
         WHERE market_ticker = 'KXSELFTEST071-26NOV07AAABBB-P1-12';
        IF v_row.no_bid <> 0.78 OR v_row.no_ask <> 0.80 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: v_kalshi_board NO side reads %', v_row;
        END IF;

        -- ------------------------------------------------ 3. refusals
        v_ok := false;
        BEGIN
            PERFORM public.kalshi_board_markets_upsert(jsonb_build_array(jsonb_build_object(
                'market_ticker', 'KXSELFTEST071-26NOV09XXXYYY-A', 'event_ticker', 'KXSELFTEST071-26NOV09XXXYYY',
                'market_kind', 'prop')), v_t1);
        EXCEPTION WHEN foreign_key_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a market with no event was accepted'; END IF;

        v_ok := false;
        BEGIN
            PERFORM public.kalshi_board_markets_upsert(jsonb_build_array(jsonb_build_object(
                'market_ticker', 'KXSELFTEST071-26NOV07AAABBB-Z', 'event_ticker', 'KXSELFTEST071-26NOV07AAABBB',
                'market_kind', 'prop')), v_t1, true);
        EXCEPTION WHEN foreign_key_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a test market under a real event was accepted'; END IF;

        v_ok := false;
        BEGIN
            PERFORM public.kalshi_board_markets_upsert(jsonb_build_array(jsonb_build_object(
                'market_ticker', 'KXSELFTEST071-26NOV07AAABBB-Z', 'event_ticker', 'KXSELFTEST071-26NOV07AAABBB',
                'market_kind', 'parlay')), v_t1);
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: market_kind parlay was accepted'; END IF;

        v_ok := false;
        BEGIN
            PERFORM public.kalshi_board_markets_upsert(jsonb_build_array(jsonb_build_object(
                'market_ticker', 'KXSELFTEST071-26NOV07AAABBB-Z', 'event_ticker', 'KXSELFTEST071-26NOV07AAABBB',
                'market_kind', 'prop', 'yes_ask', 1.5)), v_t1);
        EXCEPTION WHEN check_violation OR numeric_value_out_of_range THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a YES ask of 1.5 was accepted'; END IF;

        v_ok := false;
        BEGIN
            INSERT INTO public.kalshi_series_board (series_ticker, title, sport, fee_type, fee_multiplier, fee_regime)
            VALUES ('E2EREAL', 'x', 'x', 'quadratic', 1, '{}');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a real series with an E2E ticker was accepted'; END IF;

        v_ok := false;
        BEGIN
            PERFORM public.kalshi_board_series_upsert(jsonb_set(v_series, '{0,fee_type}', '"flat"'));
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: fee_type flat was accepted'; END IF;

        -- ------------------------------------------------ 4. readers
        SELECT count(*) INTO v_n FROM public.kalshi_board_sports() x WHERE x->>'sport' = 'Basketball';
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: kalshi_board_sports has no Basketball'; END IF;
        SELECT x INTO v_j FROM public.kalshi_board_games('Basketball') x
         WHERE x->>'g' = 'KXSELFTEST071GAME-26NOV07AAABBB';
        IF v_j IS NULL OR (v_j->>'p')::int <> 2 OR (v_j->>'c')::int <> 2 OR v_j ? 'l' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: kalshi_board_games reads %', v_j;
        END IF;
        SELECT x INTO v_j FROM public.kalshi_board_game('KXSELFTEST071GAME-26NOV07AAABBB') x;
        IF v_j IS NULL OR jsonb_array_length(v_j->'m') <> 2 OR v_j->'m'->0->>'lb' <> 'Player One: 10+'
           OR NOT (v_j->>'c')::boolean THEN
            RAISE EXCEPTION 'SELFTEST FAILED: kalshi_board_game reads %', v_j;
        END IF;
        SELECT count(*) INTO v_n FROM public.kalshi_board_game('KXSELFTEST071GAME-26NOV07AAABBB', 'game_line') x;
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: the kind filter let props through'; END IF;
        v_j := public.ctx_kalshi_board();
        IF coalesce((v_j->'counts'->'Basketball'->>'prop')::int, 0) < 2
           OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'top_tonight') x
                           WHERE x->>'market' = 'Player One: 12+') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_kalshi_board reads %', v_j;
        END IF;

        -- ------------------------------------------------ 5. sweep
        v_n := public.kalshi_board_sweep(ARRAY['KXSELFTEST071-26NOV07AAABBB'],
                                         ARRAY['KXSELFTEST071-26NOV07AAABBB-P1-10']);
        IF v_n <> 1 OR EXISTS (SELECT 1 FROM public.kalshi_board
                                WHERE market_ticker = 'KXSELFTEST071-26NOV07AAABBB-P1-12') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the sweep removed % rows', v_n;
        END IF;

        -- ------------------------------------------------ 6. link
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, status,
                                          espn_status, content_hash, kalshi_event_ticker, link_state, link_method)
        VALUES ('nba', 'game', '999071001', 'BBB at AAA', v_soon + interval '5 minutes', 'AAA', 'BBB',
                'scheduled', 'STATUS_SCHEDULED', md5('071-1'), 'KXSELFTEST071GAME-26NOV07AAABBB', 'linked',
                'manual')
        RETURNING id INTO v_sid;
        v_n := public.kalshi_board_link();
        SELECT sports_event_id, starts_at INTO v_row FROM public.kalshi_board_events
         WHERE event_ticker = 'KXSELFTEST071-26NOV07AAABBB';
        IF v_n < 1 OR v_row.sports_event_id <> v_sid OR v_row.starts_at <> v_soon + interval '5 minutes' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the link reads % (wrote %)', v_row, v_n;
        END IF;
        v_n := public.kalshi_board_events_upsert(jsonb_set(v_events, '{0,game_title}', '"AAA vs BBB (renamed)"'), v_t1);
        SELECT starts_at INTO v_row FROM public.kalshi_board_events WHERE event_ticker = 'KXSELFTEST071-26NOV07AAABBB';
        IF v_n <> 1 OR v_row.starts_at <> v_soon + interval '5 minutes' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an upsert overwrote the linked start (%)', v_row;
        END IF;

        -- ------------------------------------------------ 7. prune
        v_j := public.kalshi_board_prune();
        IF (v_j->>'events')::int < 1 OR (v_j->>'markets')::int < 1
           OR EXISTS (SELECT 1 FROM public.kalshi_board_events WHERE event_ticker = 'KXSELFTEST071-26NOV06CCCDDD')
           OR NOT EXISTS (SELECT 1 FROM public.kalshi_board_events WHERE event_ticker = 'KXSELFTEST071-26NOV07AAABBB')
        THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the prune read % and kept the wrong events', v_j;
        END IF;

        -- ------------------------------------------------ 8. the test helper
        v_ok := false;
        BEGIN
            PERFORM public.add_test_kalshi_board(v_marker, v_soon);
        EXCEPTION WHEN raise_exception THEN
            v_ok := SQLERRM LIKE 'add_test_kalshi_board: the owner and a verified test marker only%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: add_test_kalshi_board ran with no session'; END IF;
        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);
        INSERT INTO public.e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(v_marker, 'UTF8')), 'hex'));
        v_game := public.add_test_kalshi_board(v_marker, v_soon);
        SELECT count(*) INTO v_n FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
         WHERE e.game_ticker = v_game AND b.is_test;
        IF v_n <> 5 THEN RAISE EXCEPTION 'SELFTEST FAILED: the test game has % markets', v_n; END IF;
        SELECT count(*) INTO v_n FROM public.kalshi_board_games('Basketball') x WHERE x->>'g' = v_game;
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: a test game shows without p_tests'; END IF;
        SELECT count(*) INTO v_n FROM public.kalshi_board_games('Basketball', NULL, false, true) x
         WHERE x->>'g' = v_game AND (x->>'test')::boolean;
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: the test game is missing with p_tests'; END IF;
        v_j := public.ctx_kalshi_board();
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'top_tonight') x WHERE x->>'market' LIKE 'Test %') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_kalshi_board shows a test market';
        END IF;
        -- An old TEST event: the real prune never touches it; the test prune does,
        -- and keeps the upcoming test game.
        INSERT INTO public.kalshi_board_events (event_ticker, series_ticker, game_ticker, game_title, milestone_start,
                                                starts_at, is_test, fetched_at)
        VALUES ('E2ETEST-SELFTESTOLD', 'E2ETEST', 'E2ETEST-SELFTESTOLD', 'Old test game', v_old, v_old, true, v_t1);
        v_j := public.kalshi_board_prune();
        IF NOT EXISTS (SELECT 1 FROM public.kalshi_board_events WHERE event_ticker = 'E2ETEST-SELFTESTOLD') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the real prune removed a test event (%)', v_j;
        END IF;
        v_j := public.kalshi_board_prune(true);
        IF (v_j->>'events')::int <> 1
           OR EXISTS (SELECT 1 FROM public.kalshi_board_events WHERE event_ticker = 'E2ETEST-SELFTESTOLD')
           OR NOT EXISTS (SELECT 1 FROM public.kalshi_board_events WHERE game_ticker = v_game) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the test prune read % (want the old test event only)', v_j;
        END IF;

        -- ------------------------------------------------ 9. access
        IF has_table_privilege('anon', 'public.kalshi_board', 'SELECT,INSERT,UPDATE,DELETE')
           OR has_table_privilege('anon', 'public.kalshi_board_events', 'SELECT,INSERT,UPDATE,DELETE')
           OR has_table_privilege('anon', 'public.kalshi_series_board', 'SELECT,INSERT,UPDATE,DELETE')
           OR has_table_privilege('anon', 'public.v_kalshi_board', 'SELECT') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: anon holds a privilege';
        END IF;
        IF NOT has_table_privilege('authenticated', 'public.kalshi_board', 'SELECT')
           OR has_table_privilege('authenticated', 'public.kalshi_board', 'INSERT,UPDATE,DELETE,TRUNCATE')
           OR has_table_privilege('authenticated', 'public.kalshi_board_events', 'INSERT,UPDATE,DELETE,TRUNCATE')
           OR has_table_privilege('authenticated', 'public.kalshi_series_board', 'INSERT,UPDATE,DELETE,TRUNCATE')
           OR has_table_privilege('authenticated', 'public.v_kalshi_board', 'INSERT,UPDATE,DELETE') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: authenticated is not select-only';
        END IF;
        IF NOT has_table_privilege('valemont_readonly', 'public.kalshi_board', 'SELECT')
           OR NOT has_table_privilege('valemont_readonly', 'public.kalshi_board_events', 'SELECT')
           OR NOT has_table_privilege('valemont_readonly', 'public.kalshi_series_board', 'SELECT')
           OR NOT has_table_privilege('valemont_readonly', 'public.v_kalshi_board', 'SELECT') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: valemont_readonly cannot read';
        END IF;
        SELECT count(*) INTO v_n FROM pg_class c
         WHERE c.oid IN ('public.kalshi_board'::regclass, 'public.kalshi_board_events'::regclass,
                         'public.kalshi_series_board'::regclass)
           AND c.relrowsecurity
           AND (SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.tablename = c.relname
                   AND p.cmd = 'SELECT' AND p.roles = '{authenticated}') = 1
           AND (SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.tablename = c.relname) = 1;
        IF v_n <> 3 THEN RAISE EXCEPTION 'SELFTEST FAILED: RLS or the owner policy is missing (% of 3)', v_n; END IF;
        IF has_function_privilege('authenticated', 'public.kalshi_board_markets_upsert(jsonb, timestamptz, boolean)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.kalshi_board_prune(boolean)', 'EXECUTE')
           OR has_function_privilege('authenticated', 'public.kalshi_board_prune(boolean)', 'EXECUTE')
           OR has_function_privilege('valemont_readonly', 'public.kalshi_board_sweep(text[], text[], boolean)', 'EXECUTE')
           OR has_function_privilege('authenticated', 'public.kalshi_board_link(boolean)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.ctx_kalshi_board()', 'EXECUTE')
           OR has_function_privilege('anon', 'public.kalshi_board_games(text, text, boolean, boolean)', 'EXECUTE')
           OR has_function_privilege('valemont_readonly', 'public.add_test_kalshi_board(text, timestamptz)', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.kalshi_board_game(text, text, boolean, boolean)', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.ctx_kalshi_board()', 'EXECUTE') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: function grants';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.context_providers
                        WHERE key = 'kalshi_board' AND NOT brief_section AND enabled AND sort = 48) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: kalshi_board is not an enabled, non-brief provider at sort 48';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '071 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '071_kalshi_board';
-- (b) SELECT c.relname, c.relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname),
--            has_table_privilege('anon', c.oid, 'SELECT'), has_table_privilege('authenticated', c.oid, 'SELECT'),
--            has_table_privilege('authenticated', c.oid, 'INSERT,UPDATE,DELETE'),
--            has_table_privilege('valemont_readonly', c.oid, 'SELECT'), c.reloptions
--       FROM pg_class c WHERE c.relname IN ('kalshi_series_board', 'kalshi_board_events', 'kalshi_board',
--                                          'v_kalshi_board');
-- (c) SELECT p.proname, has_function_privilege('anon', p.oid, 'EXECUTE'),
--            has_function_privilege('authenticated', p.oid, 'EXECUTE'),
--            has_function_privilege('valemont_readonly', p.oid, 'EXECUTE'), p.prosecdef
--       FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
--      WHERE n.nspname = 'public' AND (p.proname LIKE 'kalshi_board%' OR p.proname IN ('ctx_kalshi_board',
--                                                                                     'add_test_kalshi_board'))
--      ORDER BY 1;
-- (d) SELECT key, brief_section, enabled, sort FROM context_providers WHERE key = 'kalshi_board';
-- (e) SELECT (SELECT count(*) FROM kalshi_series_board), (SELECT count(*) FROM kalshi_board_events),
--            (SELECT count(*) FROM kalshi_board);   -- 0, 0, 0 until the worker deploys
