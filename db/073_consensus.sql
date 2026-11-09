-- 073_consensus.sql: the consensus engine's tables and its one pricing
-- function (Chat 8 Phase 2; owner decisions, Nov 9; docs/consensus.md).
--
-- Every BINARY market on the Kalshi board (payout = 'binary', db/072) gets a
-- consensus probability, its source tier, the Kalshi ask, the fee and the net
-- edge per side. Read-only to every venue: nothing here places a bet
-- (CLAUDE.md §1).
--
-- The method (owner, Nov 9; frozen in Phase 4's pre-registration, no tuning
-- on outcomes):
--   tier 'books'  : the Odds API, an EXACT line at >= 2 books. p_books = the
--                   median across books of each book's POWER-de-vigged
--                   probability (multiplicative stored as a comparison only).
--   tier 'espn'   : ESPN's one book (DraftKings, the `close` line), de-vigged
--                   the same way, flagged 1 book.
--   tier 'kalshi' : no outside line. p = Kalshi's own mid.
--   p_kalshi      = the mid (yes_bid + yes_ask) / 2, NORMALIZED across an
--                   event's markets when Kalshi flags the event mutually
--                   exclusive, every market is quoted and the mids sum within
--                   0.90-1.10 (two teams; soccer's three ways incl. the draw);
--                   a ladder rung (an event not mutually exclusive) uses its
--                   plain mid, never normalized across rungs. The sum band
--                   guards an exclusive but NOT exhaustive event (a cricket
--                   match whose draw has no market: mids sum ~0.79, Nov 9).
--   p_consensus   = 0.7 x p_outside + 0.3 x p_kalshi when an outside line
--                   exists; p_kalshi when not.
--   edge(side)    = p(side) - (ask(side) + taker_fee(ask(side))): never the
--                   mid, never before fees (venues/kalshi/edge.py's gate).
--                   NO is bought at no_ask = 1 - yes_bid, p(NO) = 1 - p.
--   taker fee     = ceil_6dp(0.07 x multiplier x P x (1 - P)) (fees.py).
-- A kalshi-tier edge is <= 0 in practice (the spread plus the fee); it is
-- still a valid leg.
--
-- What it adds:
--   1. kalshi_board_events.mutually_exclusive (Kalshi's event flag; NULL until
--      the worker reads it) and kalshi_board_events_upsert writing it (absent
--      keeps the stored value: backward-compatible with the deployed worker).
--   2. odds_api_calls: append-only, TRUNCATE-guarded; every Odds API call,
--      free ones too, with its x-requests-last / -used / -remaining.
--      odds_api_usage(): this month's spend and the last remaining (the
--      worker's credit guard and the /betting credits line).
--   3. book_lines: append-only, TRUNCATE-guarded: each pull's book prices per
--      game, market and subject (every book's line, both prices and its
--      power-de-vigged p), written only when they differ from the latest row.
--      The inputs of a future rung model (owner, Nov 9): NOT used by Phase 2.
--   4. leg_consensus: outside-line rows only (books or espn), upserted
--      changed-only, deleted with their board market (FK cascade: the board's
--      sweep and its start + 6 h prune), and swept per game by the worker.
--   5. kalshi_taker_fee, consensus_leg (the blend and the edges, one place)
--      and leg_consensus_for(games): every binary market of the named games
--      with its tier, p, ask, fee and edge per side. v_leg_consensus is
--      leg_consensus_for(NULL) (the whole board; ad hoc only).
--   6. kalshi_board_game: each binary market carries its consensus ('q').
--   7. ctx_consensus(): provider `consensus` (sort 49), NOT a brief section.
--   8. add_test_kalshi_board: the test game's moneyline event mutually
--      exclusive, and a books-tier consensus row on its home market.
--   9. Access: RLS on the four tables, owner SELECT only; writers executable
--      by no API role; valemont_readonly reads.
-- Then the migration_log row and the rolled-back self-test.
--
-- Backward-compatible with the deployed worker: it sends no
-- mutually_exclusive, and nothing else it calls changes signature.

-- ===================================================================== 1
ALTER TABLE public.kalshi_board_events ADD COLUMN IF NOT EXISTS mutually_exclusive boolean;
COMMENT ON COLUMN public.kalshi_board_events.mutually_exclusive IS
    'Kalshi''s event flag (GET /events): the event''s markets cannot both resolve YES. NULL = not read yet. '
    'The consensus normalizes an exclusive event''s mids (db/073).';

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
                expected_expiration_time, combo_eligible, combo_yes_only, combo_collections, combo_size_max,
                combo_quoters, mutually_exclusive, is_test, fetched_at)
        SELECT r.event_ticker, r.series_ticker, r.game_ticker, r.game_title, r.milestone_start,
               r.milestone_start, r.close_time, r.expected_expiration_time, coalesce(r.combo_eligible, false),
               coalesce(r.combo_yes_only, false), coalesce(r.combo_collections, '{}'), r.combo_size_max,
               r.combo_quoters, r.mutually_exclusive, p_is_test, p_at
          FROM jsonb_to_recordset(p_rows) AS r (
                   event_ticker text, series_ticker text, game_ticker text, game_title text,
                   milestone_start timestamptz, close_time timestamptz, expected_expiration_time timestamptz,
                   combo_eligible boolean, combo_yes_only boolean, combo_collections text[],
                   combo_size_max smallint, combo_quoters smallint, mutually_exclusive boolean)
        ON CONFLICT (event_ticker) DO UPDATE SET
            series_ticker = EXCLUDED.series_ticker, game_ticker = EXCLUDED.game_ticker,
            game_title = EXCLUDED.game_title, milestone_start = EXCLUDED.milestone_start,
            -- A linked event keeps ESPN's start (kalshi_board_link); else the milestone's.
            starts_at = CASE WHEN e.sports_event_id IS NULL THEN EXCLUDED.milestone_start ELSE e.starts_at END,
            close_time = EXCLUDED.close_time, expected_expiration_time = EXCLUDED.expected_expiration_time,
            combo_eligible = EXCLUDED.combo_eligible, combo_yes_only = EXCLUDED.combo_yes_only,
            combo_collections = EXCLUDED.combo_collections, combo_size_max = EXCLUDED.combo_size_max,
            combo_quoters = EXCLUDED.combo_quoters,
            -- Absent (NULL) keeps what is stored: a refresh never forgets the flag.
            mutually_exclusive = coalesce(EXCLUDED.mutually_exclusive, e.mutually_exclusive),
            fetched_at = greatest(e.fetched_at, EXCLUDED.fetched_at)
         WHERE e.is_test = p_is_test
           AND (e.series_ticker, e.game_ticker, e.game_title, e.milestone_start, e.close_time,
                e.expected_expiration_time, e.combo_eligible, e.combo_yes_only, e.combo_collections,
                e.combo_size_max, e.combo_quoters, e.mutually_exclusive)
               IS DISTINCT FROM
               (EXCLUDED.series_ticker, EXCLUDED.game_ticker, EXCLUDED.game_title, EXCLUDED.milestone_start,
                EXCLUDED.close_time, EXCLUDED.expected_expiration_time, EXCLUDED.combo_eligible,
                EXCLUDED.combo_yes_only, EXCLUDED.combo_collections, EXCLUDED.combo_size_max,
                EXCLUDED.combo_quoters, coalesce(EXCLUDED.mutually_exclusive, e.mutually_exclusive))
        RETURNING 1
    )
    SELECT count(*)::integer FROM w;
$$;

-- ===================================================================== 2
CREATE TABLE IF NOT EXISTS odds_api_calls (
    id          BIGSERIAL    PRIMARY KEY,
    -- The path template, never a query string (the key rides there):
    -- /sports, /sports/:sport/events, /sports/:sport/odds, /sports/:sport/events/:event/odds.
    endpoint    TEXT         NOT NULL CHECK (endpoint ~ '^/[a-z0-9_/:]{1,80}$'),
    sport       TEXT         CHECK (sport IS NULL OR sport ~ '^[a-z0-9_]{1,60}$'),
    event_id    TEXT         CHECK (event_id IS NULL OR event_id ~ '^[A-Za-z0-9]{1,64}$'),
    -- (A regex repetition count tops out at 255: the length is checked apart.)
    markets     TEXT         CHECK (markets IS NULL OR (markets ~ '^[a-z0-9_,]+$' AND char_length(markets) <= 400)),
    regions     TEXT         CHECK (regions IS NULL OR regions ~ '^[a-z0-9_,]{1,40}$'),
    status      SMALLINT     NOT NULL CHECK (status BETWEEN 100 AND 599),
    -- x-requests-last / -used / -remaining; NULL when the header was absent.
    cost        INTEGER      CHECK (cost IS NULL OR cost >= 0),
    used        INTEGER      CHECK (used IS NULL OR used >= 0),
    remaining   INTEGER      CHECK (remaining IS NULL OR remaining >= 0),
    bytes       INTEGER      NOT NULL DEFAULT 0 CHECK (bytes >= 0),
    is_test     BOOLEAN      NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS odds_api_calls_created ON odds_api_calls (created_at);
COMMENT ON TABLE odds_api_calls IS
    'Every Odds API call (free ones too) with its credit headers. Append-only. The worker''s guard refuses a paid '
    'call at remaining < 50 or a month''s spend > 450 (db/073; docs/consensus.md).';

DROP TRIGGER IF EXISTS odds_api_calls_stamp ON odds_api_calls;
CREATE TRIGGER odds_api_calls_stamp
    BEFORE INSERT ON odds_api_calls
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_stamp_created();
DROP TRIGGER IF EXISTS odds_api_calls_immutable ON odds_api_calls;
CREATE TRIGGER odds_api_calls_immutable
    BEFORE UPDATE OR DELETE ON odds_api_calls
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS odds_api_calls_no_truncate ON odds_api_calls;
CREATE TRIGGER odds_api_calls_no_truncate
    BEFORE TRUNCATE ON odds_api_calls
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- This calendar month (UTC) of real calls: credits spent (sum of cost), calls,
-- and the last remaining any real call reported. The account's own reset date
-- may differ from the calendar month (the owner reads it off the account page).
CREATE OR REPLACE FUNCTION public.odds_api_usage()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH m AS (SELECT date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS start)
    SELECT jsonb_build_object(
        'month_start', m.start,
        'spent', coalesce((SELECT sum(c.cost) FROM public.odds_api_calls c
                            WHERE NOT c.is_test AND c.created_at >= m.start), 0),
        'calls', (SELECT count(*) FROM public.odds_api_calls c WHERE NOT c.is_test AND c.created_at >= m.start),
        'remaining', (SELECT c.remaining FROM public.odds_api_calls c
                       WHERE NOT c.is_test AND c.remaining IS NOT NULL ORDER BY c.id DESC LIMIT 1),
        'last_at', (SELECT max(c.created_at) FROM public.odds_api_calls c WHERE NOT c.is_test),
        'allowance', 500)
      FROM m;
$$;

-- ===================================================================== 3
CREATE TABLE IF NOT EXISTS book_lines (
    id               BIGSERIAL    PRIMARY KEY,
    source           TEXT         NOT NULL CHECK (source IN ('odds_api', 'espn')),
    league           TEXT         NOT NULL CHECK (league ~ '^[a-z0-9_]{1,20}$'),
    -- No foreign key: sports_events rows are pruned; this history is not.
    sports_event_id  BIGINT,
    event_key        TEXT         NOT NULL CHECK (event_key ~ '^[A-Za-z0-9_-]{1,64}$'),
    starts_at        TIMESTAMPTZ  NOT NULL,
    market           TEXT         NOT NULL CHECK (market ~ '^[a-z0-9_]{1,60}$'),
    -- The team or player the prices are for (a team's side of h2h/spreads,
    -- a player's over); NULL for a game total.
    subject          TEXT         CHECK (subject IS NULL OR char_length(subject) BETWEEN 1 AND 120),
    -- {book: [line|null, price_a, price_b|null, p_power|null]}: American
    -- prices for (subject or over, other side or under); p_power is the
    -- power-de-vigged probability of the first side (null: one-sided).
    lines            JSONB        NOT NULL CHECK (jsonb_typeof(lines) = 'object' AND lines <> '{}'::jsonb
                                                  AND octet_length(lines::text) <= 4000),
    fetched_at       TIMESTAMPTZ  NOT NULL,
    is_test          BOOLEAN      NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS book_lines_key ON book_lines (event_key, market, (coalesce(subject, '')), id DESC);
COMMENT ON TABLE book_lines IS
    'Book prices per game, market and subject, each pull, written only when they differ from the latest row. '
    'Append-only history: the inputs of a future rung model, pre-registered in Phase 4 (owner, Nov 9). db/073.';

DROP TRIGGER IF EXISTS book_lines_stamp ON book_lines;
CREATE TRIGGER book_lines_stamp
    BEFORE INSERT ON book_lines
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_stamp_created();
DROP TRIGGER IF EXISTS book_lines_immutable ON book_lines;
CREATE TRIGGER book_lines_immutable
    BEFORE UPDATE OR DELETE ON book_lines
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS book_lines_no_truncate ON book_lines;
CREATE TRIGGER book_lines_no_truncate
    BEFORE TRUNCATE ON book_lines
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- Appends each row whose lines differ from the latest stored row for the same
-- source, game, market and subject. Returns how many it wrote.
CREATE OR REPLACE FUNCTION public.book_lines_append(p_rows jsonb, p_is_test boolean DEFAULT false)
RETURNS integer
LANGUAGE sql
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH r AS (
        SELECT * FROM jsonb_to_recordset(p_rows) AS r (
                   source text, league text, sports_event_id bigint, event_key text, starts_at timestamptz,
                   market text, subject text, lines jsonb, fetched_at timestamptz)
    ),
    w AS (
        INSERT INTO public.book_lines (source, league, sports_event_id, event_key, starts_at, market, subject, lines,
                                       fetched_at, is_test)
        SELECT r.source, r.league, r.sports_event_id, r.event_key, r.starts_at, r.market, r.subject, r.lines,
               r.fetched_at, p_is_test
          FROM r
         WHERE r.lines IS DISTINCT FROM (
                   SELECT l.lines FROM public.book_lines l
                    WHERE l.event_key = r.event_key AND l.market = r.market
                      AND coalesce(l.subject, '') = coalesce(r.subject, '')
                      AND l.source = r.source AND l.is_test = p_is_test
                    ORDER BY l.id DESC LIMIT 1)
        RETURNING 1
    )
    SELECT count(*)::integer FROM w;
$$;

-- ===================================================================== 4
CREATE TABLE IF NOT EXISTS leg_consensus (
    market_ticker  TEXT          PRIMARY KEY REFERENCES kalshi_board (market_ticker) ON DELETE CASCADE,
    tier           TEXT          NOT NULL CHECK (tier IN ('books', 'espn')),
    -- Books with the exact line (books: >= 2; espn: exactly 1).
    n_books        SMALLINT      NOT NULL CHECK (n_books BETWEEN 1 AND 40),
    p_books        NUMERIC(7, 6) CHECK (p_books IS NULL OR (p_books > 0 AND p_books < 1)),
    -- The multiplicative de-vig's median: a reported comparison, never used.
    p_books_mult   NUMERIC(7, 6) CHECK (p_books_mult IS NULL OR (p_books_mult > 0 AND p_books_mult < 1)),
    p_espn         NUMERIC(7, 6) CHECK (p_espn IS NULL OR (p_espn > 0 AND p_espn < 1)),
    -- Compact: each book's line and de-vigged p, the method. Never refetched.
    inputs         JSONB         NOT NULL CHECK (jsonb_typeof(inputs) = 'object' AND octet_length(inputs::text) <= 2000),
    -- When the outside line was fetched (moves only when the row's values change).
    source_at      TIMESTAMPTZ   NOT NULL,
    computed_at    TIMESTAMPTZ   NOT NULL,
    CONSTRAINT leg_consensus_tier_shape CHECK (
        (tier = 'books' AND n_books >= 2 AND p_books IS NOT NULL)
        OR (tier = 'espn' AND n_books = 1 AND p_espn IS NOT NULL AND p_books IS NULL AND p_books_mult IS NULL))
);
COMMENT ON TABLE leg_consensus IS
    'Outside-line consensus inputs per binary board market (books or espn tier). Kalshi-tier markets have no row: '
    'leg_consensus_for() derives them from kalshi_board with the same function. Deleted with the market. db/073.';

-- Upserts outside-line rows for markets on the board (a market not on the
-- board, or of the other is_test, is skipped). Changed-only; returns the count.
CREATE OR REPLACE FUNCTION public.leg_consensus_upsert(p_rows jsonb, p_at timestamptz, p_is_test boolean DEFAULT false)
RETURNS integer
LANGUAGE sql
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH w AS (
        INSERT INTO public.leg_consensus AS c
               (market_ticker, tier, n_books, p_books, p_books_mult, p_espn, inputs, source_at, computed_at)
        SELECT r.market_ticker, r.tier, r.n_books, r.p_books, r.p_books_mult, r.p_espn, r.inputs, r.source_at, p_at
          FROM jsonb_to_recordset(p_rows) AS r (
                   market_ticker text, tier text, n_books smallint, p_books numeric, p_books_mult numeric,
                   p_espn numeric, inputs jsonb, source_at timestamptz)
          JOIN public.kalshi_board b ON b.market_ticker = r.market_ticker
         WHERE b.is_test = p_is_test AND b.payout = 'binary'
        ON CONFLICT (market_ticker) DO UPDATE SET
            tier = EXCLUDED.tier, n_books = EXCLUDED.n_books, p_books = EXCLUDED.p_books,
            p_books_mult = EXCLUDED.p_books_mult, p_espn = EXCLUDED.p_espn, inputs = EXCLUDED.inputs,
            source_at = EXCLUDED.source_at, computed_at = EXCLUDED.computed_at
         WHERE (c.tier, c.n_books, c.p_books, c.p_books_mult, c.p_espn, c.inputs)
               IS DISTINCT FROM
               (EXCLUDED.tier, EXCLUDED.n_books, EXCLUDED.p_books, EXCLUDED.p_books_mult, EXCLUDED.p_espn,
                EXCLUDED.inputs)
        RETURNING 1
    )
    SELECT count(*)::integer FROM w;
$$;

-- The rows of the named games that this pull did not confirm (a line gone, a
-- book count fallen under 2). Returns the count.
CREATE OR REPLACE FUNCTION public.leg_consensus_sweep(p_games text[], p_keep text[], p_is_test boolean DEFAULT false)
RETURNS integer
LANGUAGE sql
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH d AS (
        DELETE FROM public.leg_consensus c
         USING public.kalshi_board b, public.kalshi_board_events e
         WHERE b.market_ticker = c.market_ticker AND e.event_ticker = b.event_ticker
           AND b.is_test = p_is_test AND e.game_ticker = ANY(coalesce(p_games, '{}'))
           AND NOT (c.market_ticker = ANY(coalesce(p_keep, '{}')))
        RETURNING 1
    )
    SELECT count(*)::integer FROM d;
$$;

-- ===================================================================== 5
-- Kalshi's taker fee per contract at a price, rounded up at $0.000001
-- (venues/kalshi/fees.py FeeRegime.taker_fee). NULL off (0, 1).
CREATE OR REPLACE FUNCTION public.kalshi_taker_fee(p_price numeric, p_mult numeric)
RETURNS numeric
LANGUAGE sql
IMMUTABLE
SET search_path = ''
AS $$
    SELECT CASE WHEN p_price IS NULL OR p_mult IS NULL OR p_price <= 0 OR p_price >= 1 THEN NULL
                ELSE ceil(0.07 * p_mult * p_price * (1 - p_price) * 1000000) / 1000000 END;
$$;

-- The blend and the edges, in one place (frozen weights 0.7 / 0.3).
-- p_out NULL: the kalshi tier (p = p_kalshi). p_kalshi NULL: no consensus.
CREATE OR REPLACE FUNCTION public.consensus_leg(p_out numeric, p_kalshi numeric, p_yes_bid numeric,
                                                p_yes_ask numeric, p_mult numeric)
RETURNS TABLE (p_consensus numeric, fee_yes numeric, fee_no numeric, edge_yes numeric, edge_no numeric)
LANGUAGE sql
IMMUTABLE
SET search_path = ''
AS $$
    WITH p AS (
        SELECT round(CASE WHEN p_kalshi IS NULL THEN NULL
                          WHEN p_out IS NULL THEN p_kalshi
                          ELSE 0.7 * p_out + 0.3 * p_kalshi END, 6) AS p,
               CASE WHEN p_yes_bid IS NULL THEN NULL ELSE 1 - p_yes_bid END AS no_ask
    ),
    f AS (
        SELECT p.p, p.no_ask, public.kalshi_taker_fee(p_yes_ask, p_mult) AS fy,
               public.kalshi_taker_fee(p.no_ask, p_mult) AS fn
          FROM p
    )
    SELECT f.p, f.fy, f.fn, f.p - (p_yes_ask + f.fy), (1 - f.p) - (f.no_ask + f.fn) FROM f;
$$;

-- Every binary market of the named games (NULL: the whole board; ad hoc
-- only), each with its tier, p and edges. The one implementation the view,
-- the page and Wags read.
CREATE OR REPLACE FUNCTION public.leg_consensus_for(p_games text[])
RETURNS TABLE (market_ticker text, event_ticker text, game_ticker text, series_ticker text, sport text,
               league text, starts_at timestamptz, market_kind text, subject text, stat text, line numeric,
               label text, is_test boolean, tier text, n_books smallint, p_books numeric, p_books_mult numeric,
               p_espn numeric, p_kalshi numeric, normalized boolean, p_consensus numeric, yes_bid numeric,
               yes_ask numeric, no_ask numeric, fee_yes numeric, fee_no numeric, edge_yes numeric,
               edge_no numeric, inputs jsonb, computed_at timestamptz)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH m AS (
        SELECT b.market_ticker, b.event_ticker, e.game_ticker, e.series_ticker, s.sport, s.league, e.starts_at,
               b.market_kind, b.subject, b.stat, b.line, b.label, b.is_test, b.yes_bid, b.yes_ask,
               b.last_changed_at, s.fee_multiplier, e.mutually_exclusive,
               (b.yes_bid + b.yes_ask) / 2 AS mid,
               sum((b.yes_bid + b.yes_ask) / 2) OVER w AS mid_sum,
               count(*) OVER w AS n_ev,
               count(b.yes_bid + b.yes_ask) OVER w AS n_quoted
          FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
          JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
         WHERE b.payout = 'binary' AND (p_games IS NULL OR e.game_ticker = ANY(p_games))
        WINDOW w AS (PARTITION BY b.event_ticker)
    ),
    k AS (
        SELECT m.*,
               coalesce(m.mutually_exclusive AND m.n_ev >= 2 AND m.n_quoted = m.n_ev
                        AND m.mid_sum BETWEEN 0.9 AND 1.1, false) AS norm
          FROM m
    ),
    kk AS (
        SELECT k.*, round(CASE WHEN k.norm THEN k.mid / k.mid_sum ELSE k.mid END, 6) AS pk FROM k
    )
    SELECT kk.market_ticker, kk.event_ticker, kk.game_ticker, kk.series_ticker, kk.sport, kk.league, kk.starts_at,
           kk.market_kind, kk.subject, kk.stat, kk.line, kk.label, kk.is_test,
           coalesce(c.tier, 'kalshi'), coalesce(c.n_books, 0)::smallint, c.p_books, c.p_books_mult, c.p_espn,
           kk.pk, kk.norm, q.p_consensus, kk.yes_bid, kk.yes_ask, 1 - kk.yes_bid, q.fee_yes, q.fee_no,
           q.edge_yes, q.edge_no, c.inputs, coalesce(c.computed_at, kk.last_changed_at)
      FROM kk
      LEFT JOIN public.leg_consensus c ON c.market_ticker = kk.market_ticker
     CROSS JOIN LATERAL public.consensus_leg(CASE c.tier WHEN 'books' THEN c.p_books WHEN 'espn' THEN c.p_espn END,
                                             kk.pk, kk.yes_bid, kk.yes_ask, kk.fee_multiplier) q;
$$;

CREATE OR REPLACE VIEW v_leg_consensus WITH (security_invoker = true) AS
SELECT * FROM public.leg_consensus_for(NULL);

-- ===================================================================== 6
-- db/072's reader; each binary market gains 'q' (its consensus):
--   t: tier b | e | k; n: books (omitted for kalshi); p: p_consensus;
--   sd: the better side y | n; e: that side's net edge; a: its ask; f: its fee.
CREATE OR REPLACE FUNCTION public.kalshi_board_game(p_game text, p_kind text DEFAULT NULL,
                                                    p_combo_only boolean DEFAULT false,
                                                    p_tests boolean DEFAULT false)
RETURNS SETOF jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH q AS (
        SELECT x.market_ticker, x.tier, x.n_books, x.p_consensus,
               CASE WHEN coalesce(x.edge_no, -9) > coalesce(x.edge_yes, -9) THEN 'n' ELSE 'y' END AS sd,
               x.edge_yes, x.edge_no, x.yes_ask, x.no_ask, x.fee_yes, x.fee_no
          FROM public.leg_consensus_for(ARRAY[p_game]) x
    ),
    m AS (
        SELECT e.event_ticker, s.title AS series_title, e.combo_eligible, e.combo_yes_only, e.combo_size_max,
               e.fetched_at, b.payout,
               b.market_kind, b.variant, b.label, b.subject, b.line, b.yes_bid, b.yes_ask, b.taker_fee_at_ask,
               CASE b.market_kind WHEN 'game_line' THEN 0 WHEN 'prop' THEN 1 ELSE 2 END AS k_order,
               CASE WHEN q.market_ticker IS NOT NULL THEN jsonb_strip_nulls(jsonb_build_object(
                   't', left(q.tier, 1), 'n', nullif(q.n_books, 0), 'p', q.p_consensus, 'sd', q.sd,
                   'e', CASE q.sd WHEN 'y' THEN q.edge_yes ELSE q.edge_no END,
                   'a', CASE q.sd WHEN 'y' THEN q.yes_ask ELSE q.no_ask END,
                   'f', CASE q.sd WHEN 'y' THEN q.fee_yes ELSE q.fee_no END)) END AS cq
          FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
          JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
          LEFT JOIN q ON q.market_ticker = b.market_ticker
         WHERE e.game_ticker = p_game AND (NOT b.is_test OR p_tests)
           AND (p_kind IS NULL OR b.market_kind = p_kind)
           AND (NOT p_combo_only OR (e.combo_eligible AND b.payout = 'binary'))
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
               'e', m.event_ticker, 'sr', min(m.series_title),
               'c', CASE WHEN bool_or(m.combo_eligible AND m.payout = 'binary') THEN true END,
               'y', CASE WHEN bool_or(m.combo_yes_only AND m.payout = 'binary') THEN true END,
               'sm', max(m.combo_size_max) FILTER (WHERE m.payout = 'binary'),
               'asof', max(m.fetched_at),
               'm', jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                        'k', m.market_kind, 'lb', m.label, 'b', m.yes_bid, 'a', m.yes_ask,
                        'f', m.taker_fee_at_ask, 'v', m.variant,
                        's', CASE WHEN m.payout = 'scalar' THEN true END, 'q', m.cq))
                    ORDER BY m.k_order, m.subject NULLS LAST, m.line NULLS FIRST, m.label)))
      FROM m
     GROUP BY m.event_ticker
     ORDER BY min(m.k_order), min(m.series_title), m.event_ticker;
$$;

-- ===================================================================== 7
CREATE OR REPLACE FUNCTION public.ctx_consensus()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH z AS (SELECT coalesce((SELECT timezone FROM public.app_settings LIMIT 1), 'America/Chicago') AS tz),
    up AS (
        SELECT s.sport, b.market_ticker, e.game_ticker, e.starts_at, c.tier
          FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
          JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
          LEFT JOIN public.leg_consensus c ON c.market_ticker = b.market_ticker
         WHERE NOT b.is_test AND b.payout = 'binary' AND e.starts_at > now()
    ),
    best AS (
        SELECT x.*, greatest(coalesce(x.edge_yes, -9), coalesce(x.edge_no, -9)) AS edge
          -- Never NULL (NULL is the whole board): no game, no rows.
          FROM public.leg_consensus_for(coalesce((SELECT array_agg(DISTINCT up.game_ticker) FROM up
                                                    WHERE up.tier IS NOT NULL
                                                      AND up.starts_at < now() + interval '12 hours'),
                                                 '{}'::text[])) x
         WHERE x.tier <> 'kalshi' AND NOT x.is_test AND x.starts_at > now()
         ORDER BY edge DESC
         LIMIT 10
    )
    SELECT jsonb_build_object(
        'about', 'The consensus engine: each binary Kalshi market''s probability from de-vigged sportsbook lines '
                 '(books tier: the median of 2+ books at the exact line; espn tier: ESPN''s one book) blended '
                 '0.7 / 0.3 with Kalshi''s mid, or Kalshi''s own mid when no book has the exact line (kalshi '
                 'tier). Net edge is that probability minus the Kalshi ask and its taker fee; a kalshi-tier '
                 'edge is <= 0. Edge is not profit and not advice; nothing is ever bet automatically.',
        'as_of', (SELECT max(c.computed_at) FROM public.leg_consensus c
                    JOIN public.kalshi_board b ON b.market_ticker = c.market_ticker WHERE NOT b.is_test),
        'counts', coalesce((SELECT jsonb_object_agg(t.sport, t.tiers) FROM (
                      SELECT u.sport, jsonb_object_agg(u.tier, u.n) AS tiers
                        FROM (SELECT up.sport, coalesce(up.tier, 'kalshi') AS tier, count(*) AS n
                                FROM up GROUP BY 1, 2) u
                       GROUP BY u.sport) t), '{}'::jsonb),
        'best_edges_tonight', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                                  'market', public.ctx_trim(b.label, 100), 'sport', b.sport, 'tier', b.tier,
                                  'books', b.n_books,
                                  'at', to_char(b.starts_at AT TIME ZONE z.tz, 'HH24:MI'),
                                  'side', CASE WHEN coalesce(b.edge_no, -9) > coalesce(b.edge_yes, -9)
                                               THEN 'no' ELSE 'yes' END,
                                  'p', b.p_consensus,
                                  'ask', CASE WHEN coalesce(b.edge_no, -9) > coalesce(b.edge_yes, -9)
                                              THEN b.no_ask ELSE b.yes_ask END,
                                  'edge', b.edge))
                                  ORDER BY b.edge DESC)
                                FROM best b CROSS JOIN z), '[]'::jsonb),
        'credits', public.odds_api_usage());
$$;

INSERT INTO context_providers (key, label, fn, brief_section, sort)
VALUES ('consensus', 'Consensus', 'public.ctx_consensus()'::regprocedure, false, 49)
ON CONFLICT (key) DO NOTHING;

-- ===================================================================== 8
-- db/072's test game; its moneyline event is mutually exclusive and its home
-- market carries a books-tier row (p_books 0.66 from 3 books).
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
    v_lad  text;
BEGIN
    IF NOT coalesce(public.e2e_marker_ok(p_marker), false) THEN
        RAISE EXCEPTION 'add_test_kalshi_board: the owner and a verified test marker only';
    END IF;
    v_game := 'E2ETEST-' || v_tag;
    v_pts := 'E2ETEST-' || v_tag || 'PTS';
    v_lad := 'E2ETEST-' || v_tag || 'LAD';
    INSERT INTO public.kalshi_series_board (series_ticker, title, sport, tags, fee_type, fee_multiplier, fee_regime,
                                            combo_eligible, collection_tickers, is_test)
    VALUES ('E2ETEST', 'E2E Test Series', 'Basketball', ARRAY['Basketball'], 'quadratic', 1,
            '{"fee_type": "quadratic", "fee_multiplier": "1"}'::jsonb, true, ARRAY['E2ECOLL-R'], true)
    ON CONFLICT (series_ticker) DO NOTHING;
    INSERT INTO public.kalshi_board_events (event_ticker, series_ticker, game_ticker, game_title, milestone_start,
                                            starts_at, combo_eligible, combo_yes_only, combo_collections,
                                            combo_size_max, combo_quoters, mutually_exclusive, is_test, fetched_at)
    VALUES (v_game, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, true, true,
            ARRAY['E2ECOLL-R'], 1, 0, true, true, now()),
           (v_pts, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, false, false,
            '{}', NULL, NULL, false, true, now()),
           (v_lad, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, true, false,
            ARRAY['E2ECOLL-R'], NULL, NULL, false, true, now());
    INSERT INTO public.kalshi_board (market_ticker, event_ticker, market_kind, variant, payout, subject, stat, line,
                                     label, yes_bid, yes_ask, volume, open_interest, taker_fee_at_ask, is_test,
                                     fetched_at, last_changed_at)
    SELECT v.market_ticker, v.event_ticker, v.market_kind, v.variant, v.payout, v.subject, v.stat, v.line, v.label,
           v.yes_bid, v.yes_ask, v.volume, v.open_interest, v.fee, true, now(), now()
      FROM (VALUES
        (v_game || '-HOM', v_game, 'game_line', NULL::text, 'binary', 'Test Home', 'winner', NULL::numeric,
         'Test Home', 0.6100::numeric, 0.6300::numeric, 1200::numeric, 800::numeric, 0.016317::numeric),
        (v_game || '-AWY', v_game, 'game_line', NULL, 'binary', 'Test Away', 'winner', NULL, 'Test Away',
         0.3700, 0.3900, 900, 700, 0.016653),
        (v_pts || '-P1-10', v_pts, 'prop', NULL, 'binary', 'Test Player', 'points', 9.5, 'Test Player: 10+',
         0.5500, 0.5800, 300, 200, 0.017052),
        (v_pts || '-P1-20', v_pts, 'prop', NULL, 'binary', 'Test Player', 'points', 19.5, 'Test Player: 20+',
         0.1500, 0.1700, 150, 100, 0.009877),
        (v_pts || '-P1-10P', v_pts, 'prop', 'protected', 'binary', 'Test Player', 'points', 9.5, 'Test Player: 10+',
         0.5400, 0.5700, 50, 40, 0.017157),
        (v_lad || '-P1', v_lad, 'prop', 'ladder', 'scalar', 'Test Player', 'points', NULL,
         'Test Player: points, $1 at 50', 0.3000, 0.3200, 20, 10, 0.015232)
      ) AS v (market_ticker, event_ticker, market_kind, variant, payout, subject, stat, line, label, yes_bid, yes_ask,
              volume, open_interest, fee);
    INSERT INTO public.leg_consensus (market_ticker, tier, n_books, p_books, p_books_mult, p_espn, inputs, source_at,
                                      computed_at)
    VALUES (v_game || '-HOM', 'books', 3, 0.660000, 0.659000, NULL,
            '{"m": "power", "b": {"draftkings": 0.66, "fanduel": 0.665, "betmgm": 0.655}}'::jsonb, now(), now());
    RETURN v_game;
END;
$$;

-- ===================================================================== 9
-- Access.
ALTER TABLE odds_api_calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE book_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE leg_consensus ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON odds_api_calls, book_lines, leg_consensus FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE odds_api_calls_id_seq, book_lines_id_seq FROM anon, authenticated, PUBLIC;
REVOKE ALL ON v_leg_consensus FROM anon, authenticated, PUBLIC;
GRANT SELECT ON odds_api_calls, book_lines, leg_consensus, v_leg_consensus TO authenticated;
GRANT SELECT ON odds_api_calls, book_lines, leg_consensus, v_leg_consensus TO valemont_readonly;
DROP POLICY IF EXISTS odds_api_calls_owner_read ON odds_api_calls;
CREATE POLICY odds_api_calls_owner_read ON odds_api_calls FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS book_lines_owner_read ON book_lines;
CREATE POLICY book_lines_owner_read ON book_lines FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS leg_consensus_owner_read ON leg_consensus;
CREATE POLICY leg_consensus_owner_read ON leg_consensus FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));

REVOKE ALL ON FUNCTION public.book_lines_append(jsonb, boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.leg_consensus_upsert(jsonb, timestamptz, boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.leg_consensus_sweep(text[], text[], boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.odds_api_usage() FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.kalshi_taker_fee(numeric, numeric) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.consensus_leg(numeric, numeric, numeric, numeric, numeric) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.leg_consensus_for(text[]) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.ctx_consensus() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.odds_api_usage() TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.kalshi_taker_fee(numeric, numeric) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.consensus_leg(numeric, numeric, numeric, numeric, numeric)
    TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.leg_consensus_for(text[]) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.ctx_consensus() TO authenticated, valemont_readonly;
-- kalshi_board_events_upsert, kalshi_board_game and add_test_kalshi_board keep
-- db/071's grants (CREATE OR REPLACE).

-- ===================================================================== 10
INSERT INTO migration_log (name) VALUES ('073_consensus')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 11
-- Self-test, run on paste, rolled back ('selftest-rollback'). Each expected
-- refusal runs in its own nested block and counts only with its SQLSTATE or
-- constraint. Real rows use series KXSELFTEST073 (no real series has it).
-- Every statement that calls a writing function stores its result first and
-- reads back in a later statement (db/062's rule); every INSERT tuple was
-- checked against its column list before handover (db/054's rule).
DO $$
DECLARE
    v_owner   uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_marker  text := 'selftest-073-' || gen_random_uuid();
    v_t0      timestamptz := now() - interval '1 minute';
    v_t1      timestamptz := now();
    v_soon    timestamptz := now() + interval '2 hours';
    v_ok      boolean;
    v_c       text;
    v_n       int;
    v_j       jsonb;
    v_game    text;
    v_row     record;
    v_id      bigint;
    v_gt      text := 'KXSELFTEST073GAME-26NOV09AAABBB';
    v_ml      text := 'KXSELFTEST073-26NOV09AAABBB';
    v_sx      text := 'KXSELFTEST073X-26NOV09AAABBB';
    v_lad     text := 'KXSELFTEST073P-26NOV09AAABBB';
    v_thin    text := 'KXSELFTEST073T-26NOV09AAABBB';
    v_series  jsonb := '[{"series_ticker": "KXSELFTEST073", "title": "Selftest 073 ML", "sport": "Basketball",
                          "league": "nba", "category": "Sports", "tags": ["Basketball"], "fee_type": "quadratic",
                          "fee_multiplier": "1", "fee_regime": {"fee_type": "quadratic"}, "combo_eligible": false,
                          "collection_tickers": []},
                         {"series_ticker": "KXSELFTEST073X", "title": "Selftest 073 3way", "sport": "Soccer",
                          "league": null, "category": "Sports", "tags": ["Soccer"], "fee_type": "quadratic",
                          "fee_multiplier": "1", "fee_regime": {"fee_type": "quadratic"}, "combo_eligible": false,
                          "collection_tickers": []},
                         {"series_ticker": "KXSELFTEST073P", "title": "Selftest 073 Props", "sport": "Basketball",
                          "league": "nba", "category": "Sports", "tags": ["Basketball"], "fee_type": "quadratic",
                          "fee_multiplier": "1", "fee_regime": {"fee_type": "quadratic"}, "combo_eligible": false,
                          "collection_tickers": []},
                         {"series_ticker": "KXSELFTEST073T", "title": "Selftest 073 Thin", "sport": "Cricket",
                          "league": null, "category": "Sports", "tags": ["Cricket"], "fee_type": "quadratic",
                          "fee_multiplier": "1", "fee_regime": {"fee_type": "quadratic"}, "combo_eligible": false,
                          "collection_tickers": []}]';
    v_events  jsonb;
    v_mk      jsonb;
    v_cons    jsonb;
    v_bl      jsonb;
BEGIN
    BEGIN
        IF v_owner IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: app_settings has no owner';
        END IF;

        -- ------------------------------------------------ 1. the fee and the blend
        IF public.kalshi_taker_fee(0.63, 1) <> 0.016317 OR public.kalshi_taker_fee(0.055, 1) <> 0.003639
           OR public.kalshi_taker_fee(0.50, 1) <> 0.0175 OR public.kalshi_taker_fee(0.50, 0.5) <> 0.00875
           OR public.kalshi_taker_fee(0, 1) IS NOT NULL OR public.kalshi_taker_fee(1, 1) IS NOT NULL
           OR public.kalshi_taker_fee(NULL, 1) IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: kalshi_taker_fee';
        END IF;
        SELECT * INTO v_row FROM public.consensus_leg(0.66, 0.62, 0.61, 0.63, 1);
        -- p = 0.7 * 0.66 + 0.3 * 0.62 = 0.648; YES 0.648 - (0.63 + 0.016317);
        -- NO (1 - 0.648) - (0.39 + 0.016653).
        IF v_row.p_consensus <> 0.648 OR v_row.fee_yes <> 0.016317 OR v_row.fee_no <> 0.016653
           OR v_row.edge_yes <> 0.001683 OR v_row.edge_no <> -0.054653 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: consensus_leg books reads %', v_row;
        END IF;
        SELECT * INTO v_row FROM public.consensus_leg(NULL, 0.62, 0.61, 0.63, 1);
        IF v_row.p_consensus <> 0.62 OR v_row.edge_yes <> -0.026317 OR v_row.edge_no <> -0.026653 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: consensus_leg kalshi tier reads %', v_row;
        END IF;
        SELECT * INTO v_row FROM public.consensus_leg(0.66, NULL, 0.61, 0.63, 1);
        IF v_row.p_consensus IS NOT NULL OR v_row.edge_yes IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no Kalshi mid still priced: %', v_row;
        END IF;
        SELECT * INTO v_row FROM public.consensus_leg(NULL, 0.005, 0.00, 0.01, 1);
        IF v_row.edge_no IS NOT NULL OR v_row.edge_yes IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a NO ask of 1.00 was priced: %', v_row;
        END IF;

        -- ------------------------------------------------ 2. the board rows
        v_n := public.kalshi_board_series_upsert(v_series);
        IF v_n <> 4 THEN RAISE EXCEPTION 'SELFTEST FAILED: series insert wrote %', v_n; END IF;
        v_events := jsonb_build_array(
            jsonb_build_object('event_ticker', v_ml, 'series_ticker', 'KXSELFTEST073', 'game_ticker', v_gt,
                               'game_title', 'AAA vs BBB', 'milestone_start', v_soon, 'mutually_exclusive', true),
            jsonb_build_object('event_ticker', v_sx, 'series_ticker', 'KXSELFTEST073X', 'game_ticker', v_gt,
                               'game_title', 'AAA vs BBB', 'milestone_start', v_soon, 'mutually_exclusive', true),
            jsonb_build_object('event_ticker', v_lad, 'series_ticker', 'KXSELFTEST073P', 'game_ticker', v_gt,
                               'game_title', 'AAA vs BBB', 'milestone_start', v_soon, 'mutually_exclusive', false),
            -- Exclusive but not exhaustive (mids sum 0.80): never normalized.
            jsonb_build_object('event_ticker', v_thin, 'series_ticker', 'KXSELFTEST073T', 'game_ticker', v_gt,
                               'game_title', 'AAA vs BBB', 'milestone_start', v_soon, 'mutually_exclusive', true));
        v_n := public.kalshi_board_events_upsert(v_events, v_t0);
        IF v_n <> 4 THEN RAISE EXCEPTION 'SELFTEST FAILED: event insert wrote %', v_n; END IF;
        -- The deployed worker sends no flag: unchanged, nothing written, the flag kept.
        v_n := public.kalshi_board_events_upsert(
            (SELECT jsonb_agg(x - 'mutually_exclusive') FROM jsonb_array_elements(v_events) x), v_t1);
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: an absent flag rewrote % events', v_n; END IF;
        SELECT mutually_exclusive INTO v_ok FROM public.kalshi_board_events WHERE event_ticker = v_ml;
        IF v_ok IS DISTINCT FROM true THEN RAISE EXCEPTION 'SELFTEST FAILED: the flag was forgotten (%)', v_ok; END IF;
        -- An absent flag beside a real change: the change is written, the flag kept.
        v_n := public.kalshi_board_events_upsert(jsonb_set(
            (SELECT jsonb_agg(x - 'mutually_exclusive') FROM jsonb_array_elements(v_events) x),
            '{0,game_title}', '"AAA vs BBB (renamed)"'), v_t1);
        SELECT game_title, mutually_exclusive INTO v_row FROM public.kalshi_board_events WHERE event_ticker = v_ml;
        IF v_n <> 1 OR v_row.mutually_exclusive IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a title change dropped the flag: % (wrote %)', v_row, v_n;
        END IF;
        v_n := public.kalshi_board_events_upsert(jsonb_set(v_events, '{2,mutually_exclusive}', 'true'), v_t1);
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: a changed flag (and the title back) wrote %', v_n; END IF;
        v_n := public.kalshi_board_events_upsert(v_events, v_t1);
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: the flag back wrote %', v_n; END IF;

        v_mk := jsonb_build_array(
            -- Two teams, mids 0.62 + 0.36 = 0.98.
            jsonb_build_object('market_ticker', v_ml || '-AAA', 'event_ticker', v_ml, 'market_kind', 'game_line',
                               'subject', 'AAA', 'label', 'AAA', 'yes_bid', 0.61, 'yes_ask', 0.63,
                               'taker_fee_at_ask', 0.016317),
            jsonb_build_object('market_ticker', v_ml || '-BBB', 'event_ticker', v_ml, 'market_kind', 'game_line',
                               'subject', 'BBB', 'label', 'BBB', 'yes_bid', 0.35, 'yes_ask', 0.37,
                               'taker_fee_at_ask', 0.016317),
            -- Three ways, mids 0.40 + 0.30 + 0.32 = 1.02.
            jsonb_build_object('market_ticker', v_sx || '-AAA', 'event_ticker', v_sx, 'market_kind', 'game_line',
                               'subject', 'AAA', 'label', 'AAA', 'yes_bid', 0.39, 'yes_ask', 0.41),
            jsonb_build_object('market_ticker', v_sx || '-BBB', 'event_ticker', v_sx, 'market_kind', 'game_line',
                               'subject', 'BBB', 'label', 'BBB', 'yes_bid', 0.29, 'yes_ask', 0.31),
            jsonb_build_object('market_ticker', v_sx || '-TIE', 'event_ticker', v_sx, 'market_kind', 'game_line',
                               'subject', 'Tie', 'label', 'Tie', 'yes_bid', 0.31, 'yes_ask', 0.33),
            -- Ladder rungs (not exclusive): plain mids 0.55 and 0.43, summing
            -- 0.98 (inside the band, so only the flag keeps them plain).
            jsonb_build_object('market_ticker', v_lad || '-P1-10', 'event_ticker', v_lad, 'market_kind', 'prop',
                               'subject', 'Player One', 'stat', 'points', 'line', 9.5, 'label', 'Player One: 10+',
                               'yes_bid', 0.54, 'yes_ask', 0.56),
            jsonb_build_object('market_ticker', v_lad || '-P1-20', 'event_ticker', v_lad, 'market_kind', 'prop',
                               'subject', 'Player One', 'stat', 'points', 'line', 19.5, 'label', 'Player One: 20+',
                               'yes_bid', 0.42, 'yes_ask', 0.44),
            jsonb_build_object('market_ticker', v_lad || '-P1LAD', 'event_ticker', v_lad, 'market_kind', 'prop',
                               'variant', 'ladder', 'payout', 'scalar', 'subject', 'Player One', 'stat', 'points',
                               'label', 'Player One: points, $1 at 50', 'yes_bid', 0.30, 'yes_ask', 0.32),
            jsonb_build_object('market_ticker', v_thin || '-AAA', 'event_ticker', v_thin, 'market_kind', 'game_line',
                               'subject', 'AAA', 'label', 'AAA', 'yes_bid', 0.39, 'yes_ask', 0.41),
            jsonb_build_object('market_ticker', v_thin || '-BBB', 'event_ticker', v_thin, 'market_kind', 'game_line',
                               'subject', 'BBB', 'label', 'BBB', 'yes_bid', 0.39, 'yes_ask', 0.41));
        v_n := public.kalshi_board_markets_upsert(v_mk, v_t0);
        IF v_n <> 10 THEN RAISE EXCEPTION 'SELFTEST FAILED: market insert wrote %', v_n; END IF;

        -- ------------------------------------------------ 3. normalization and the kalshi tier
        SELECT x.p_kalshi, x.normalized, x.tier, x.n_books, x.p_consensus INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_ml || '-AAA';
        IF v_row.p_kalshi <> round(0.62 / 0.98, 6) OR NOT v_row.normalized OR v_row.tier <> 'kalshi'
           OR v_row.n_books <> 0 OR v_row.p_consensus <> v_row.p_kalshi THEN
            RAISE EXCEPTION 'SELFTEST FAILED: two-team normalization reads %', v_row;
        END IF;
        SELECT x.p_kalshi, x.normalized INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_sx || '-TIE';
        IF v_row.p_kalshi <> round(0.32 / 1.02, 6) OR NOT v_row.normalized THEN
            RAISE EXCEPTION 'SELFTEST FAILED: three-way normalization reads %', v_row;
        END IF;
        SELECT x.p_kalshi, x.normalized INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_lad || '-P1-20';
        IF v_row.p_kalshi <> 0.43 OR v_row.normalized THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a ladder rung was normalized: %', v_row;
        END IF;
        SELECT x.p_kalshi, x.normalized INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_thin || '-AAA';
        IF v_row.p_kalshi <> 0.40 OR v_row.normalized THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a non-exhaustive event was normalized: %', v_row;
        END IF;
        SELECT count(*) INTO v_n FROM public.leg_consensus_for(ARRAY[v_gt]) x;
        IF v_n <> 9 THEN RAISE EXCEPTION 'SELFTEST FAILED: leg_consensus_for has % rows (scalar left out?)', v_n; END IF;
        SELECT count(*) INTO v_n FROM public.leg_consensus_for(ARRAY[v_gt]) x
         WHERE x.tier = 'kalshi' AND (x.edge_yes > 0 OR x.edge_no > 0);
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: % kalshi-tier markets show a positive edge', v_n; END IF;
        -- An unquoted market drops its exclusive event to plain mids, even when
        -- the quoted mids alone sum inside the band (0.66 + 0.30 = 0.96).
        v_n := public.kalshi_board_markets_upsert(
            jsonb_set(jsonb_set(jsonb_set(v_mk, '{4,yes_bid}', 'null'), '{2,yes_bid}', '0.65'), '{2,yes_ask}', '0.67'),
            v_t1);
        SELECT x.p_kalshi, x.normalized INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_sx || '-AAA';
        IF v_n <> 2 OR v_row.p_kalshi <> 0.66 OR v_row.normalized THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an unquoted leg kept normalization: % (wrote %)', v_row, v_n;
        END IF;
        v_n := public.kalshi_board_markets_upsert(v_mk, v_t1);

        -- ------------------------------------------------ 4. leg_consensus writes
        v_cons := jsonb_build_array(
            jsonb_build_object('market_ticker', v_ml || '-AAA', 'tier', 'books', 'n_books', 3, 'p_books', 0.66,
                               'p_books_mult', 0.659, 'inputs', '{"m": "power"}'::jsonb, 'source_at', v_t0),
            jsonb_build_object('market_ticker', v_sx || '-TIE', 'tier', 'espn', 'n_books', 1, 'p_espn', 0.30,
                               'inputs', '{"m": "power"}'::jsonb, 'source_at', v_t0),
            -- Not on the board: skipped.
            jsonb_build_object('market_ticker', v_ml || '-ZZZ', 'tier', 'books', 'n_books', 2, 'p_books', 0.5,
                               'inputs', '{}'::jsonb, 'source_at', v_t0),
            -- A scalar market is never a leg: skipped.
            jsonb_build_object('market_ticker', v_lad || '-P1LAD', 'tier', 'espn', 'n_books', 1, 'p_espn', 0.5,
                               'inputs', '{}'::jsonb, 'source_at', v_t0));
        v_n := public.leg_consensus_upsert(v_cons, v_t0);
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: consensus insert wrote %', v_n; END IF;
        v_n := public.leg_consensus_upsert(v_cons, v_t1);
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: unchanged consensus rewrote %', v_n; END IF;
        v_n := public.leg_consensus_upsert(jsonb_set(v_cons, '{0,p_books}', '0.5'), v_t1, true);
        SELECT c.p_books INTO v_row FROM public.leg_consensus c WHERE c.market_ticker = v_ml || '-AAA';
        IF v_n <> 0 OR v_row.p_books <> 0.66 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test write touched a real market: % (wrote %)', v_row, v_n;
        END IF;
        v_n := public.leg_consensus_upsert(jsonb_set(v_cons, '{0,p_books}', '0.67'), v_t1);
        SELECT c.p_books, c.computed_at INTO v_row FROM public.leg_consensus c WHERE c.market_ticker = v_ml || '-AAA';
        IF v_n <> 1 OR v_row.p_books <> 0.67 OR v_row.computed_at <> v_t1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a changed p read % (wrote %)', v_row, v_n;
        END IF;
        SELECT x.tier, x.n_books, x.p_consensus, x.edge_yes, x.p_kalshi INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_ml || '-AAA';
        IF v_row.tier <> 'books' OR v_row.n_books <> 3
           OR v_row.p_consensus <> round(0.7 * 0.67 + 0.3 * v_row.p_kalshi, 6)
           OR v_row.edge_yes <> v_row.p_consensus - (0.63 + 0.016317) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the books tier reads %', v_row;
        END IF;
        SELECT x.tier, x.n_books, x.p_consensus INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_sx || '-TIE';
        IF v_row.tier <> 'espn' OR v_row.n_books <> 1
           OR v_row.p_consensus <> round(0.7 * 0.30 + 0.3 * round(0.32 / 1.02, 6), 6) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the espn tier reads %', v_row;
        END IF;
        IF (SELECT count(*) FROM public.v_leg_consensus v WHERE v.game_ticker = v_gt) <> 9 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: v_leg_consensus disagrees with leg_consensus_for';
        END IF;

        -- Refusals by the tier shape, each by its constraint.
        v_c := NULL;
        BEGIN
            PERFORM public.leg_consensus_upsert(jsonb_set(v_cons, '{0,n_books}', '1'), v_t1);
        EXCEPTION WHEN check_violation THEN GET STACKED DIAGNOSTICS v_c = CONSTRAINT_NAME;
        END;
        IF v_c IS DISTINCT FROM 'leg_consensus_tier_shape' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a one-book books row was not refused by the shape (%)', v_c;
        END IF;
        v_c := NULL;
        BEGIN
            PERFORM public.leg_consensus_upsert(jsonb_set(v_cons, '{1,p_books}', '0.3'), v_t1);
        EXCEPTION WHEN check_violation THEN GET STACKED DIAGNOSTICS v_c = CONSTRAINT_NAME;
        END;
        IF v_c IS DISTINCT FROM 'leg_consensus_tier_shape' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an espn row with p_books was not refused by the shape (%)', v_c;
        END IF;
        v_c := NULL;
        BEGIN
            PERFORM public.leg_consensus_upsert(jsonb_set(v_cons, '{0,tier}', '"kalshi"'), v_t1);
        EXCEPTION WHEN check_violation THEN GET STACKED DIAGNOSTICS v_c = CONSTRAINT_NAME;
        END;
        IF v_c IS DISTINCT FROM 'leg_consensus_tier_check' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a stored kalshi tier was not refused (%)', v_c;
        END IF;
        v_c := NULL;
        BEGIN
            PERFORM public.leg_consensus_upsert(jsonb_set(v_cons, '{0,p_books}', '1'), v_t1);
        EXCEPTION WHEN check_violation THEN GET STACKED DIAGNOSTICS v_c = CONSTRAINT_NAME;
        END;
        IF v_c IS DISTINCT FROM 'leg_consensus_p_books_check' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: p_books = 1 was not refused (%)', v_c;
        END IF;

        -- The sweep: only the named games, only what was not kept.
        v_n := public.leg_consensus_sweep(ARRAY[v_gt], ARRAY[v_ml || '-AAA']);
        IF v_n <> 1 OR NOT EXISTS (SELECT 1 FROM public.leg_consensus WHERE market_ticker = v_ml || '-AAA') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the sweep removed % (kept row gone?)', v_n;
        END IF;
        v_n := public.leg_consensus_sweep(ARRAY['KXSELFTEST073GAME-NOSUCH'], '{}');
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: a sweep of another game removed %', v_n; END IF;
        -- The board's sweep cascades.
        BEGIN
            v_n := public.kalshi_board_sweep(ARRAY[v_ml], ARRAY[v_ml || '-BBB']);
        EXCEPTION WHEN foreign_key_violation OR not_null_violation THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the board sweep did not cascade to leg_consensus (%)', SQLERRM;
        END;
        IF v_n <> 1 OR EXISTS (SELECT 1 FROM public.leg_consensus WHERE market_ticker = v_ml || '-AAA') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a swept market kept its consensus (%)', v_n;
        END IF;

        -- ------------------------------------------------ 5. kalshi_board_game carries q
        v_n := public.kalshi_board_markets_upsert(v_mk, v_t1);
        v_n := public.leg_consensus_upsert(v_cons, v_t1);
        SELECT x INTO v_j FROM public.kalshi_board_game(v_gt) x WHERE x->>'e' = v_ml;
        IF (SELECT m->'q'->>'t' FROM jsonb_array_elements(v_j->'m') m WHERE m->>'lb' = 'AAA') IS DISTINCT FROM 'b'
           OR (SELECT (m->'q'->>'n')::int FROM jsonb_array_elements(v_j->'m') m WHERE m->>'lb' = 'AAA') <> 3
           OR (SELECT m->'q'->>'t' FROM jsonb_array_elements(v_j->'m') m WHERE m->>'lb' = 'BBB') IS DISTINCT FROM 'k'
           OR (SELECT m->'q' ? 'n' FROM jsonb_array_elements(v_j->'m') m WHERE m->>'lb' = 'BBB') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: kalshi_board_game q reads %', v_j;
        END IF;
        SELECT x INTO v_j FROM public.kalshi_board_game(v_gt) x WHERE x->>'e' = v_lad;
        IF (SELECT count(*) FROM jsonb_array_elements(v_j->'m') m WHERE m ? 'q') <> 2
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'m') m WHERE (m->>'s')::boolean AND m ? 'q') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a scalar market carries q, or a rung lacks it: %', v_j;
        END IF;

        -- ------------------------------------------------ 6. odds_api_calls
        INSERT INTO public.odds_api_calls (endpoint, sport, markets, regions, status, cost, used, remaining, bytes)
        VALUES ('/sports/:sport/odds', 'americanfootball_nfl', 'h2h,spreads,totals', 'us', 200, 3, 11, 489, 74501);
        INSERT INTO public.odds_api_calls (endpoint, sport, markets, regions, status, cost, used, remaining, bytes)
        VALUES ('/sports/:sport/odds', 'basketball_nba', 'h2h,spreads,totals', 'us', 200, 3, 14, 486, 48940)
        RETURNING id INTO v_id;
        SELECT created_at INTO v_row FROM public.odds_api_calls WHERE id = v_id;
        v_j := public.odds_api_usage();
        IF (v_j->>'spent')::int < 6 OR (v_j->>'remaining')::int <> 486 OR (v_j->>'allowance')::int <> 500 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: odds_api_usage reads %', v_j;
        END IF;
        v_ok := false;
        BEGIN
            UPDATE public.odds_api_calls SET cost = 0 WHERE id = v_id;
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%append-only%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: an odds_api_calls UPDATE was accepted'; END IF;
        v_ok := false;
        BEGIN
            DELETE FROM public.odds_api_calls WHERE id = v_id;
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%append-only%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: an odds_api_calls DELETE was accepted'; END IF;
        v_ok := false;
        BEGIN
            TRUNCATE public.odds_api_calls;
        EXCEPTION WHEN raise_exception THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: odds_api_calls TRUNCATE was accepted'; END IF;
        v_c := NULL;
        BEGIN
            INSERT INTO public.odds_api_calls (endpoint, status) VALUES ('/sports?apiKey=x', 200);
        EXCEPTION WHEN check_violation THEN GET STACKED DIAGNOSTICS v_c = CONSTRAINT_NAME;
        END;
        IF v_c IS DISTINCT FROM 'odds_api_calls_endpoint_check' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an endpoint with a query string was accepted (%)', v_c;
        END IF;

        -- ------------------------------------------------ 7. book_lines
        v_bl := jsonb_build_array(
            jsonb_build_object('source', 'odds_api', 'league', 'nba', 'event_key', 'selftest073abc',
                               'starts_at', v_soon, 'market', 'h2h', 'subject', 'AAA',
                               'lines', '{"draftkings": [null, -160, 135, 0.6012]}'::jsonb, 'fetched_at', v_t0),
            jsonb_build_object('source', 'odds_api', 'league', 'nba', 'event_key', 'selftest073abc',
                               'starts_at', v_soon, 'market', 'totals',
                               'lines', '{"draftkings": [221.5, -110, -110, 0.5]}'::jsonb, 'fetched_at', v_t0));
        v_n := public.book_lines_append(v_bl);
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: book_lines insert wrote %', v_n; END IF;
        v_n := public.book_lines_append(v_bl);
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: unchanged book lines appended %', v_n; END IF;
        v_n := public.book_lines_append(jsonb_set(v_bl, '{1,lines}', '{"draftkings": [222.5, -110, -110, 0.5]}'));
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: a moved total appended %', v_n; END IF;
        v_n := public.book_lines_append(v_bl, true);
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: test rows compared against real ones (%)', v_n; END IF;
        v_ok := false;
        BEGIN
            DELETE FROM public.book_lines WHERE event_key = 'selftest073abc';
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%append-only%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a book_lines DELETE was accepted'; END IF;
        v_ok := false;
        BEGIN
            TRUNCATE public.book_lines;
        EXCEPTION WHEN raise_exception THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: book_lines TRUNCATE was accepted'; END IF;
        v_c := NULL;
        BEGIN
            PERFORM public.book_lines_append(jsonb_set(v_bl, '{0,lines}', '{}'));
        EXCEPTION WHEN check_violation THEN GET STACKED DIAGNOSTICS v_c = CONSTRAINT_NAME;
        END;
        IF v_c IS DISTINCT FROM 'book_lines_lines_check' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: empty lines were accepted (%)', v_c;
        END IF;

        -- ------------------------------------------------ 8. ctx_consensus
        v_j := public.ctx_consensus();
        IF NOT (v_j ? 'about' AND v_j ? 'counts' AND v_j ? 'best_edges_tonight' AND v_j ? 'credits') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_consensus keys %', (SELECT array_agg(k) FROM jsonb_object_keys(v_j) k);
        END IF;
        IF coalesce((v_j->'counts'->'Basketball'->>'books')::int, 0) < 1
           OR coalesce((v_j->'counts'->'Soccer'->>'espn')::int, 0) < 1
           OR coalesce((v_j->'counts'->'Soccer'->>'kalshi')::int, 0) < 2 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_consensus counts %', v_j->'counts';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'best_edges_tonight') x
                        WHERE x->>'tier' = 'books' AND x->>'market' = 'AAA') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: best_edges_tonight lacks the books row: %', v_j->'best_edges_tonight';
        END IF;
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'best_edges_tonight') x WHERE x->>'tier' = 'kalshi') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: best_edges_tonight lists a kalshi-tier market';
        END IF;

        -- ------------------------------------------------ 9. the test helper
        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);
        INSERT INTO public.e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(v_marker, 'UTF8')), 'hex'));
        v_ok := false;
        BEGIN
            PERFORM public.add_test_kalshi_board(v_marker || '-wrong', v_soon);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE 'add_test_kalshi_board: the owner%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: the test helper took a wrong marker'; END IF;
        v_game := public.add_test_kalshi_board(v_marker, v_soon);
        SELECT x.tier, x.n_books, x.p_consensus, x.edge_yes, x.normalized INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_game]) x WHERE x.label = 'Test Home';
        IF v_row.tier <> 'books' OR v_row.n_books <> 3 OR NOT v_row.normalized OR v_row.edge_yes <= 0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the test game''s home market reads %', v_row;
        END IF;
        SELECT count(*) INTO v_n FROM public.kalshi_board_game(v_game, NULL, false, true) x,
                                      jsonb_array_elements(x->'m') m
         WHERE m ? 'q';
        IF v_n <> 5 THEN RAISE EXCEPTION 'SELFTEST FAILED: the test game has % markets with q', v_n; END IF;
        SELECT count(*) INTO v_n FROM public.ctx_consensus() c,
                                      jsonb_array_elements(c->'best_edges_tonight') x WHERE x->>'market' = 'Test Home';
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: ctx_consensus shows a test market'; END IF;
        -- The test game's books row is not counted (section 8 counted 1 real one).
        IF (public.ctx_consensus()->'counts'->'Basketball'->>'books')::int <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_consensus counts test markets: %', public.ctx_consensus()->'counts';
        END IF;

        -- ------------------------------------------------ 10. objects and grants
        IF NOT EXISTS (SELECT 1 FROM public.migration_log WHERE name = '073_consensus') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no migration_log row';
        END IF;
        IF has_table_privilege('anon', 'public.leg_consensus', 'SELECT')
           OR has_table_privilege('anon', 'public.v_leg_consensus', 'SELECT')
           OR has_table_privilege('authenticated', 'public.leg_consensus', 'INSERT,UPDATE,DELETE')
           OR has_table_privilege('authenticated', 'public.odds_api_calls', 'INSERT,UPDATE,DELETE')
           OR has_table_privilege('authenticated', 'public.book_lines', 'INSERT,UPDATE,DELETE')
           OR NOT has_table_privilege('authenticated', 'public.v_leg_consensus', 'SELECT')
           OR NOT has_table_privilege('valemont_readonly', 'public.book_lines', 'SELECT')
           OR NOT has_table_privilege('valemont_readonly', 'public.odds_api_calls', 'SELECT')
           OR has_function_privilege('authenticated', 'public.leg_consensus_upsert(jsonb, timestamptz, boolean)',
                                     'EXECUTE')
           OR has_function_privilege('authenticated', 'public.book_lines_append(jsonb, boolean)', 'EXECUTE')
           OR has_function_privilege('authenticated', 'public.leg_consensus_sweep(text[], text[], boolean)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.ctx_consensus()', 'EXECUTE')
           OR has_function_privilege('anon', 'public.leg_consensus_for(text[])', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.leg_consensus_for(text[])', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.odds_api_usage()', 'EXECUTE')
           OR has_function_privilege('authenticated', 'public.kalshi_board_events_upsert(jsonb, timestamptz, boolean)',
                                     'EXECUTE')
        THEN
            RAISE EXCEPTION 'SELFTEST FAILED: grants';
        END IF;
        IF (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.leg_consensus'::regclass) IS NOT TRUE
           OR (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.book_lines'::regclass) IS NOT TRUE
           OR (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.odds_api_calls'::regclass) IS NOT TRUE THEN
            RAISE EXCEPTION 'SELFTEST FAILED: RLS off';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '073 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '073_consensus';
-- (b) SELECT relname, relrowsecurity FROM pg_class
--      WHERE relname IN ('odds_api_calls', 'book_lines', 'leg_consensus');
-- (c) SELECT count(*), count(mutually_exclusive) FROM kalshi_board_events;      -- the flag NULL until the worker
-- (d) SELECT key, sort, brief_section, enabled FROM context_providers WHERE key = 'consensus';
-- (e) SELECT tier, count(*) FROM v_leg_consensus GROUP BY 1;                   -- all kalshi before the first run
-- (f) SELECT public.odds_api_usage();
-- (g) md5 of the function bodies against the scratch copy's.
