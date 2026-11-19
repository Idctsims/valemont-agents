# M3b reproduction: notes and caveats (2026-11-19)

Result: `m3b_nba_repro_2026-11-19.json` (script `b94dd52`). 16 of 20 nba-hub
NBA rows land within ±0.05 of ours, so the gate (15) passes and the external
table stands.

## Owner decision, 2026-11-19

The 4 rows that did not reproduce are relabelled **prior** in
`docs/correlation_rho.json` (version 2026-11-19.1; source
`nba_hub_unreproduced`, class `prior`), values unchanged. They carry the prior
chip until M3b measures them itself:

| Row | nba-hub | Ours (95% CI) | Diff |
|---|---|---|---|
| self\|ar\|td | 0.871 | 0.572 (0.526–0.621) | −0.299 |
| self\|ar\|dd | 0.862 | 0.607 (0.592–0.623) | −0.255 |
| self\|ast\|td | 0.799 | 0.561 (0.516–0.603) | −0.238 |
| self\|dd\|pra | 0.711 | 0.480 (0.466–0.497) | −0.231 |

All four pair a double-double or triple-double with a line stat, and ours is
lower every time. One dd/td row in the 20 did reproduce: self\|stl\|td
(0.094 vs ours 0.106), which stays measured. The likeliest cause, unverified:
books list double-double and triple-double markets only for players who can
make one, and our proxy counts every qualifying player.

## Mate-row bias

All four `mate` rows in the 20 came out above nba-hub's, by +0.021 to +0.048:
ast|tpm +0.035, ast|pts +0.046, ast|pa +0.048, pa|stl +0.021. Two sit within
0.004 of the ±0.05 tolerance. Every `opp` row is within ±0.024; every
`team|*|win` row is within ±0.021. The direction is consistent, so it is
probably a property of the method (who counts as a teammate with a line),
not noise. Recorded, not corrected.

## The line-proxy caveat

nba-hub measured at **book lines**; public box scores carry none. Our line is
floor(median of a player's earlier games this season) + 0.5, and a player
counts only after 10 earlier games averaging 20+ minutes (a proxy for having a
book line). Rho of a binary outcome depends on where the line sits, so:

- the 16 that reproduced agree **under this proxy**, not at book lines;
- a different, equally defensible proxy could move the two marginal mate
  rows past ±0.05 and turn 16 into 14, under the gate;
- the bootstrap intervals cover sampling error only, not the proxy choice.

Not applied: nba-hub's jackknife shrink and positive-definite projection.
The plan's n/(n+200) shrink is reported beside each raw value in the JSON.
