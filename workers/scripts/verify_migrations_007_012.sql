-- Read-only: every object from db/007–012. Expect ok = true on every row.
SELECT '007 crypto/equities/prizepicks disabled' AS item,
       (SELECT count(*) FROM agents WHERE slug IN ('crypto','equities','prizepicks') AND NOT enabled) = 3 AS ok
UNION ALL
SELECT '008 legs_outcome_check includes settled',
       coalesce((SELECT pg_get_constraintdef(oid) LIKE '%''settled''%' FROM pg_constraint
                  WHERE conrelid = 'public.legs'::regclass AND conname = 'legs_outcome_check'), false)
UNION ALL
SELECT '009 agent ' || s || ' (disabled, not test)',
       EXISTS (SELECT 1 FROM agents WHERE slug = s AND NOT enabled AND NOT is_test)
  FROM unnest(ARRAY['nfl_ml','nfl_spread','nfl_props','kalshi_collector']) AS s
UNION ALL
SELECT '010/011 table ' || t || ' with RLS on',
       coalesce((SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                  WHERE n.nspname = 'public' AND c.relname = t AND c.relkind = 'r'), false)
  FROM unnest(ARRAY['model_versions','kalshi_markets','kalshi_candles']) AS t
UNION ALL
SELECT '010/011 trigger ' || tg,
       EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = tg AND NOT tgisinternal AND tgenabled <> 'D')
  FROM unnest(ARRAY['model_versions_immutable','kalshi_markets_immutable',
                    'kalshi_candles_immutable','kalshi_candles_pregame']) AS tg
UNION ALL
SELECT '010/011 constraint ' || cn,
       EXISTS (SELECT 1 FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace
                WHERE n.nspname = 'public' AND k.conname = cn)
  FROM unnest(ARRAY['fit_sees_only_the_past','unusable_fits_say_why',
                    'kalshi_markets_ticker_key',
                    'kalshi_candles_market_id_period_minutes_end_period_ts_key']) AS cn
UNION ALL
SELECT '010 index model_versions_lookup_idx',
       to_regclass('public.model_versions_lookup_idx') IS NOT NULL
UNION ALL
SELECT '011 function kalshi_candle_pregame()',
       EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'kalshi_candle_pregame')
UNION ALL
SELECT '012 _kalshi_probe (disabled, is_test)',
       EXISTS (SELECT 1 FROM agents WHERE slug = '_kalshi_probe' AND NOT enabled AND is_test);
