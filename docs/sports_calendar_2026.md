# Sports calendar, rest of 2026 (Chat 3 Phase 3, Step 0)

Verified source data for `boost_windows` (db/042) and the golf "top player"
list. Every row comes from the official page named beside it. **Nothing here
is filled from memory**: where a source does not state a fact, the fact is
left out and the gap is said.

- **Fetched:** 2026-10-12, between 22:00 and 22:22 UTC, on the owner's
  machine (Windows PowerShell `Invoke-WebRequest` and `curl`). The
  rolltide.com, pgatour.com and owgr.com pages render with JavaScript, so a
  plain fetch sees no table. The data was read from each page's own
  server-rendered HTML or embedded JSON, or from the API that page calls.
  formula1.com and nba.com were read with a web fetch.
- **Times:** a source's local or CT times as printed. Window bounds (§5) are
  America/Chicago. Chicago is on CDT (UTC−5) until Sun 2026-11-01 02:00 and
  on CST (UTC−6) after that.

## 1. Alabama football, rest of 2026

Source: <https://rolltide.com/sports/football/schedule/text>, the text view
of the official Alabama Athletics schedule (page title "2026 Football
Schedule - Alabama Athletics"). The embedded data carries ISO dates in 2026
(for example `2026-10-17T14:30:00`) and the line "Tennessee on October 17,
2026 2:30 p.m. CT". Fetched 2026-10-12.

Played, for context (as printed):

| Date | Time | H/A | Opponent | Result |
|---|---|---|---|---|
| Sep 5 (Sat) | 11 a.m. CT | Home | East Carolina | W 48-10 |
| Sep 12 (Sat) | 2:30 p.m. CT | Away | Kentucky | W 45-17 |
| Sep 19 (Sat) | 2:30 p.m. CT | Home | Florida State | W 50-36 |
| Sep 26 (Sat) | 6 p.m. CT | Home | South Carolina | W 49-18 |
| Oct 3 (Sat) | 11 a.m. CT | Away | Mississippi State | W 56-23 |
| Oct 10 (Sat) | 6:30 p.m. CT | Home | Georgia | L 17-20 |

**Remaining** (as printed):

| Date | Time | H/A | Opponent | Site |
|---|---|---|---|---|
| Oct 17 (Sat) | 2:30 p.m. CT | Away | Tennessee | Knoxville, Tenn. |
| Oct 24 (Sat) | 2:30 p.m. CT | Home | Texas A&M | Tuscaloosa, Ala. |
| Nov 7 (Sat) | Flex | Away | LSU | Baton Rouge, La. |
| Nov 14 (Sat) | Early | Away | Vanderbilt | Nashville, Tenn. |
| Nov 21 (Sat) | 1 p.m. CT | Home | Chattanooga | Tuscaloosa, Ala. |
| Nov 28 (Sat) | Flex | Home | Auburn | Tuscaloosa, Ala. |

- **No game Oct 31** (no row on the schedule): a bye week, so no window.
- **Not on the schedule, so left out:** the SEC Championship and any College
  Football Playoff game. Neither is known until Alabama qualifies. A window
  is added in a later migration once a game is confirmed on rolltide.com.

## 2. Formula 1, rest of 2026

Source: <https://www.formula1.com/en/racing/2026>. Fetched 2026-10-12.
Rounds 1–17 show results on the page; round 18 is marked "NEXT RACE". The
page lists no sprint weekends.

| Round | Grand Prix | Dates (as printed) |
|---|---|---|
| 18 | United States | 23 - 25 Oct |
| 19 | Mexico | 30 Oct - 01 Nov |
| 20 | Brazil | 06 - 08 Nov |
| 21 | Las Vegas | 19 - 21 Nov |
| 22 | Qatar | 27 - 29 Nov |
| 23 | Abu Dhabi | 04 - 06 Dec |

The 2026 entry list, from <https://www.formula1.com/en/teams> (fetched
2026-10-12), for the move-proposal entities (`docs/sports_news.md` §5):
Mercedes, Ferrari, McLaren, Red Bull Racing, Racing Bulls, Alpine, Haas F1
Team, Audi, Williams, Aston Martin, Cadillac. Ferrari's drivers are listed
as Charles Leclerc and Lewis Hamilton, matching the db/039 links.

## 3. PGA Tour, rest of 2026

Source: <https://www.pgatour.com/schedule>, read from the page's embedded
schedule query (`tourCode R`, season `2026`, `seasonStatus ACTIVE`, 49
tournaments). Fetched 2026-10-12. The label column is the page's own
`tournamentCategoryInfo.label`. **The page does not label majors.**

Remaining (status `UPCOMING`):

| Dates (as printed) | Event | FedExCup | Purse | Page label |
|---|---|---|---|---|
| Oct 22 - 25 | Butterfield Bermuda Championship | 500 pts | $6,000,000 | none |
| Oct 29 - Nov 1 | VidantaWorld Mexico Open | 500 pts | $6,000,000 | none |
| Nov 5 - 8 | World Wide Technology Championship | 500 pts | $6,000,000 | none |
| Nov 12 - 15 | Austin Championship | 500 pts | $6,000,000 | none |
| Nov 19 - 22 | The RSM Classic | 500 pts | $7,400,000 | none |
| Dec 3 - 6 | Hero World Challenge | none listed | $5,000,000 | none |
| Dec 10 - 13 | PGA Tour Q-School presented by Korn Ferry | none listed | $1,200,000 | none |
| Dec 11 - 13 | Grant Thornton Invitational | none listed | $4,100,000 | none |

**No major, signature event or Players Championship remains in 2026.** On
this page:
- the season's events labelled "Signature Event" all finished by Jun 28
  (Travelers Championship);
- THE PLAYERS Championship finished Mar 12–15;
- the four events conventionally called majors (Masters, PGA Championship,
  U.S. Open, The Open Championship) are all `COMPLETED`.

That last point names majors from general knowledge, not from the page. It
changes nothing, because no event of any kind with those names remains.

So, by the brief's rule ("every major and the big mid-majors (Players,
signature events)"), **db/042 seeds no golf window for 2026.** Hero World
Challenge is an unofficial event with no FedExCup points; it is listed for
the owner to decide (`docs/sports_news.md` decision D6). The 2027 schedule
is not on the page yet.

## 4. OWGR top 15

Source: <https://www.owgr.com/current-world-ranking>. The rows come from the
API that page calls,
`https://apiweb.owgr.com/api/owgr/rankings/getRankings?pageSize=15&pageNumber=1&regionId=0&countryId=0&sortString=Rank+ASC`.
Fetched 2026-10-12 ~22:15 UTC. **The response does not state the ranking
week** (`weekEndDate` is null), so this list is dated by its fetch time
only. No ties.

| Rank | Player | Country | Avg points | Last week |
|---|---|---|---|---|
| 1 | Scottie Scheffler | United States | 16.9482 | 1 |
| 2 | Rory McIlroy | Northern Ireland | 8.6620 | 2 |
| 3 | Matt Fitzpatrick | England | 7.1983 | 3 |
| 4 | Cameron Young | United States | 7.1840 | 4 |
| 5 | Wyndham Clark | United States | 6.3146 | 5 |
| 6 | Tommy Fleetwood | England | 5.1669 | 7 |
| 7 | Russell Henley | United States | 5.1655 | 6 |
| 8 | Chris Gotterup | United States | 5.1590 | 8 |
| 9 | Xander Schauffele | United States | 5.1172 | 10 |
| 10 | Sam Burns | United States | 5.0986 | 9 |
| 11 | Collin Morikawa | United States | 4.8452 | 11 |
| 12 | J.J. Spaun | United States | 4.7534 | 12 |
| 13 | Viktor Hovland | Norway | 4.6953 | 13 |
| 14 | Si Woo Kim | South Korea | 4.5465 | 14 |
| 15 | Jon Rahm | Spain | 4.4694 | 15 |

- Brooks Koepka is not in the top 15. He stays a tier-1 follow from db/039.
- Scheffler already exists as an entity (tier 1, db/039), so db/042 adds 14
  people.
- The list is a snapshot. It changes only through a new migration, from a
  fresh fetch (`docs/sports_news.md` §3).

## 5. Windows these produce (db/042 seed, pending the owner's OK)

Bounds in America/Chicago. The rules are in `docs/sports_news.md` §6.

| Label | sport | entity | starts_at | ends_at | boost_hours |
|---|---|---|---|---|---|
| Alabama game week: at Tennessee | cfb | Alabama Crimson Tide | 2026-10-12 00:00 | 2026-10-19 00:00 | 12 |
| Alabama game week: Texas A&M | cfb | Alabama Crimson Tide | 2026-10-19 00:00 | 2026-10-26 00:00 | 12 |
| Alabama game week: at LSU | cfb | Alabama Crimson Tide | 2026-11-02 00:00 | 2026-11-09 00:00 | 12 |
| Alabama game week: at Vanderbilt | cfb | Alabama Crimson Tide | 2026-11-09 00:00 | 2026-11-16 00:00 | 12 |
| Alabama game week: Chattanooga | cfb | Alabama Crimson Tide | 2026-11-16 00:00 | 2026-11-23 00:00 | 12 |
| Alabama game week: Auburn | cfb | Alabama Crimson Tide | 2026-11-23 00:00 | 2026-11-30 00:00 | 12 |
| F1 race weekend: United States GP | f1 | — | 2026-10-22 00:00 | 2026-10-26 12:00 | 12 |
| F1 race weekend: Mexico GP | f1 | — | 2026-10-29 00:00 | 2026-11-02 12:00 | 12 |
| F1 race weekend: Brazil GP | f1 | — | 2026-11-05 00:00 | 2026-11-09 12:00 | 12 |
| F1 race weekend: Las Vegas GP | f1 | — | 2026-11-18 00:00 | 2026-11-22 12:00 | 12 |
| F1 race weekend: Qatar GP | f1 | — | 2026-11-26 00:00 | 2026-11-30 12:00 | 12 |
| F1 race weekend: Abu Dhabi GP | f1 | — | 2026-12-03 00:00 | 2026-12-07 12:00 | 12 |

The Tennessee window is **active now** (it opened Mon 2026-10-12 00:00
Chicago), so "stronger in game week" can be checked on the phone the day
db/042 lands.
