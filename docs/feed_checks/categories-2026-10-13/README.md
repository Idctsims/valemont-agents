# Category backfill: dry run for review (2026-10-13)

The deterministic topics -> category mapping (workers/feed/category.py),
applied offline to every kept, real Home story (749), read through
db_inspect at about 03:20 UTC on 2026-10-13. Nothing was written: the
column arrives with db/042, and scripts/feed_backfill_category.py fills it
after the paste. Stories with no topics (not summarized yet) get their
category from the summary call instead.

| category | stories |
|---|---|
| deals | 37 |
| hospitality | 60 |
| sports_business | 24 |
| film_music | 117 |
| luxury_marine | 31 |
| betting | 4 (all there are) |
| other | 138 |
| no topics yet (left for the summary call) | 338 |

Ten samples per category, random with seed 42: id, title, then the topics
that decided it. Misfits I can see: 1308 and 1559 (taxpayer and research
FUNDING -> deals); 859 (a casino redevelopment, betting over real estate
by the tie order); 1098 (whiskey: other, not luxury).

## deals: 37 stories, 10 samples (random, seed 42)
482	Monte Paschi to Hold Monday Board Meeting on M&A Situation	[m_and_a, bank_consolidation, takeover_bid]
632	Quinn Emanuel issues warning over data-center financing	[data_center_financing, ai_infrastructure, debt]
722	Efferon wants to eradicate the devastating toll of pediatric sepsis	[healthcare_startup, sepsis, medical_device]
723	What to know about the landmark Warner Bros. Discovery sale	[m_and_a, media_industry]
793	Is Leicester Cityâ€™s $264 Million Price Tag Too Rich for Hungry Investors?	[m_and_a, club_valuation, relegation]
832	Delta Air Lines Expects to Absorb $6 Billion Increase in Fuel Costs, Lowers 2026 Profit Outlook	[airline_earnings, fuel_costs, guidance_cut]
849	The Weekâ€™s 10 Biggest Funding Rounds: AI And Energy Top A Busy Lineup Of Large Rounds	[funding_round, ai_infrastructure, venture_capital]
1296	Blue Owl CEOs rebuff credit fears	[management_buyout, corporate_governance, private_credit]
1308	Judge Orders Trump Administration To Stop Running Taxpayer Funded Ads That Promote POTUS	[government_advertising, court_order, taxpayer_funds]
1559	NCAA Wonâ€™t Commit to Future Concussion Research Funding	[concussion_research, research_funding]

## hospitality: 60 stories, 10 samples (random, seed 42)
826	Travel AIâ€™s Prove-It Phase, Visibility, and the Booking Gap	[ai_adoption, travel_distribution, bookings]
827	The War Is Changing the Economics of Saudi Tourism	[tourism, geopolitics, travel_costs]
833	Accorâ€™s Gulf Hotel Recovery Is A Tale Of Two Markets	[hotel_recovery, leisure_travel, corporate_travel]
857	Inside Broward Countyâ€™s battle for health care real estate	[health_care_real_estate, commercial_real_estate]
858	South Florida Dirt: Why itâ€™s a â€œvery difficult time to buildâ€	[real_estate_development, construction_market]
1403	Blackstone Sells Fort Lauderdale Industrial For $57M: The South Florida Deal Sheet	[industrial_real_estate, m_and_a, south_florida]
1411	Citadel Headquarters Project Under Stop-Work Order Following Rig Collapse	[construction_safety, commercial_development, stop_work_order]
1414	Bugatti Lends Brand To 60-Story Miami Condo Tower	[branded_residences, condo_development, luxury_real_estate]
1439	Foreclosure looms for Brookfieldâ€™s Pembroke Lakes Mall after alleged $260M loan default	[foreclosure, commercial_real_estate, loan_default]
2285	India Is Now Skyscannerâ€™s Biggest Market, and Nearly Half Its Searches Go Abroad	[travel_search, consumer_behavior]

## sports_business: 24 stories, 10 samples (random, seed 42)
709	WNBA Finals Pairs Leagueâ€™s Most and Least Valuable Teams	[team_valuation, media_rights]
791	Royals $3 Billion Ballpark Development Gets Key Approval	[stadium_financing, ballpark_development]
794	How Oregon Beat College Footballâ€™s â€˜Impossibleâ€™ Backup QB Market	[cfb_roster_management, quarterback_market, nil]
1114	Black Out: The Bledisloe Cup, a New Tackle Law, and Jason Kelce Playing Rugby	[rugby, rule_change, sports_media]
1321	Titans Fire Three Business Execs Amid Another Ugly Start	[front_office_changes, nfl_business]
1322	Yankees Exit Leaves Boone, Cashman Questions Heading Into Likely Lockout	[front_office, labor_lockout]
1329	Arrowhead Stays, Kauffman Goes in Countyâ€™s Post-Chiefs, Royals Plan	[stadium_financing, stadium_future, local_government]
1330	Jimmy Rollins: MLB Analytics Gurus Undervalue Clubhouse Chemistry	[analytics, clubhouse_culture, front_office]
1334	WNBA Semifinals Open With 1.15M Viewers, Up 10%	[viewership, playoffs, media_rights]
1339	Sports Is Key to Skydance Becoming â€˜Leading Content Engineâ€™	[media_strategy, sports_media]

## film_music: 117 stories, 10 samples (random, seed 42)
480	Bloomberg This Weekend: Saudi Airport Incident (Podcast)	[podcast, aviation_incident]
765	â€˜KPop Demon Huntersâ€™ Gets New Tech Collection at Target, Including Ramen-Themed Chargers, Soda 	[merchandise_licensing, consumer_products]
766	Sacha Baron Cohen Confirmed To Star In Quentin Tarantinoâ€™s â€˜The Popinjay Cavalierâ€™ West End Sta	[theatre_casting, west_end_production]
841	In Canada: Backstreet Boys Set New Touring Record; Vans Warped Tour Not Returning	[touring, concert_business, venue_regulation]
1115	Have you heard? Pokimane at Chipotle, a Rooster Teeth reunion, and a Mike Tomlin Minecraft upda	[creator_economy, brand_partnership]
1142	Girls' Generation Reunites for 2027 Seasons Greetings Ahead of 20th Anniversary	[kpop, anniversary, music_release]
2308	Cineflix Rights Sells Nordic Blue Crime Hit â€˜Hildurâ€™ to Canal+ in France, Australiaâ€™s SBS in Mu	[international_sales, licensing, scripted_tv]
2350	Mirren Mack, CJ Beckford Hope to Find Themselves in Romantic Drama â€˜Arthur & Claraâ€™: Exclusive 	[film_production, first_look]
2372	Why the Boss of Leonine, Germanyâ€™s Top Indie Film and TV Company, Is Backing Skydance: â€˜David E	[media_merger, film_distribution, german_language_territories]
3892	â€˜High Potentialâ€™ Gets Post-Super Bowl Slot, Eyes â€˜Greyâ€™s Anatomyâ€™-Style 2-Parter On ABC & Disne	[programming_schedule, streaming]

## luxury_marine: 31 stories, 10 samples (random, seed 42)
1058	Sunreef 80 Power	[yacht_review]
1059	Wajer 77	[yachting]
1091	Inside Robb Reportâ€™s 50th Anniversary Issue	[magazine_anniversary, luxury_goods]
1136	Rivian: Find The Latest Rivian Stories, News & Features	[automotive]
1480	First look inside the third AB 130 yacht Casa Bri Mare	[superyachts, yacht_launch]
1481	Inside Angiola: How Maioraâ€™s 37m yacht makes space go further	[superyachts, yacht_design]
1488	The largest yachts attending the Fort Lauderdale International Boat Show 2026 so far	[yachts, boat_show]
1492	Construction underway on Sarp Yachts XSR 85 RPH motor yacht	[superyacht_construction, shipbuilding]
1493	Construction progresses on ISA Yachts' second 65m Classic model	[yacht_construction, shipbuilding]
1497	72m Damen Yachting support yacht emerges from Oceanco following extensive refit for Gabe Newell	[superyacht, refit, shipyards]

## betting: 4 stories, 10 samples (random, seed 42)
859	Billionaire Jeffrey Soffer puts chips on the table with plans for Big Easy Casino redevelopment	[casino_development, real_estate, redevelopment]
1320	NFL Escalates Prediction Market Frustrations to Supreme Court	[prediction_markets, litigation]
1335	DraftKings, Prediction Markets Put on Notice Amid FanDuel VIP Scrutiny	[sports_betting, prediction_markets]
1561	Peer-to-Fear: Prediction Markets Worry About CFTC Proposal	[prediction_markets, regulation]

## other: 138 stories, 10 samples (random, seed 42)
628	Fox stays positive on data centers even as public backlash grows	[media_coverage, data_centers]
718	48 hours to TechCrunch Disrupt 2026 â€” hear directly from the people building whatâ€™s next	[tech_conference, ticket_sales]
724	These execs think voice AI hasnâ€™t reached its ChatGPT moment yet	[voice_ai, context_layer, ai_infrastructure]
1098	Jeffersonâ€™s Gave Its Core Whiskeys a Serious Upgrade	[whiskey, product_launch]
1126	Elizabeth Hurley on Her 'Unlikely Pairing' With Billy Ray Cyrus: 'We're Actually Like Twins'	[celebrity_news, collaboration]
1131	Fetty Wap Reflects on Being 'Selfish' During Time of His Arrest	[celebrity_news, legal]
1153	Soulja Boy Teases Running for President in 2028	[celebrity_politics, presidential_run]
1154	Kevin Hart Wants One Woman Kept Out of His Court Battle â€” And It's Someone From His Past	[litigation, court_case]
1336	Kyle Juszczyk Wins Chugging Celebration Appeal, League Adjusts Policy	[nfl_discipline, league_policy]
1386	Live Nation hit with class action lawsuits over data breach involving Social Security numbers a	[data_breach, class_action, consumer_privacy]
