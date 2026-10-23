# Batch 8: town mode, live check (23 October 2026)

Town mode (`python -m forage.collectors.town_mode --town Musina --db COPY.db --from 2026-10-01`) passed its Musina acceptance: on a fresh live run into a copy of the 22 October candidate database it did at least as well as the 23 October keyword run on every Musina bench case, better on five of six, and saved far fewer rows than the 23 October sweep's four runs. The second town, Malamulele (not on the bench), was compared with a manual search and found everything the search found except one school robbery, which a word added after the check then caught.

All runs were on database copies in `/home/sprite/forge-batch8-live/`. No live database, pin, schedule, approval gate or skill was touched. Article text stayed off.

## What town mode does

- **Searches:** the town name times the crime, Priority Intelligence, infrastructure and other words the four sweeps typed by hand (`data/town_mode.json`), plus up to six gazetteer places (collector top-up places whose parent is the town or that sit in its municipality, then GeoNames sections within 15 km) and the town's extras from the same file. Every search gets `when:Nd`. Musina: 33 searches. Malamulele: 28.
- **Google News:** the Batch 4 area check, pinned to the town. A search with no recognisable town ("Beitbridge border") is checked against the town instead of keeping everything.
- **Civic Intelligence:** the town's local papers (Batch 7 sitemaps), no feed on a switched-off publisher's hosts, and only items about the town. A local-paper lead is also kept when its headline names no place at all (Neltox).
- **Report and score:** a JSON and markdown report next to the database, and for a bench town the Batch 1 scorer run on what the run saved, against three references.

## Choices made in this batch

- **Caxton feeds:** town mode never fetches the three Civic Intelligence feeds on citizen.co.za (The Citizen, Review Online, Letaba Herald). Caxton's terms (clause 2.7.1) bar automated monitoring without written authority, and the request was dropped. Plain and scheduled runs still read them; that was not changed here.
- **Recent-only limit:** added to keyword, infrastructure and PI keyword searches. Actor mode, the scheduled default, is unchanged.
- **Infrastructure preset words:** borehole, without water and sewage were added to the province terms. This changes what an infrastructure preset run searches for, including a scheduled one if any schedule uses the preset.
- **"About the town":** the town's area words in `data/town_mode.json`, or a gazetteer place within 60 km, and not one of the town's foreign look-alikes (Messina in Sicily).
- **Robbery:** added to the crime words after the Malamulele check (see below).

## Musina (bench town)

Run on the final code: 33 searches, 210 seconds, 383 rows saved.

| Reference | Caught (of 5) | Full passes | Keep-out (of 1) | Rows saved | Rows naming Musina's area |
|---|---|---|---|---|---|
| Town mode, 23 Oct 22:36 SAST | 5 | 3 | 1 | 383 | 40 |
| 23 Oct keyword run, as saved | 3 | 0 | 0 | 330 | 32 |
| 23 Oct all four runs, as saved | 4 | 1 | 0 | 2,189 | 42 |
| Today's code replayed over the 23 Oct inputs | 4 | 3 | 1 | 2,189 | 42 |

| Case | Town mode | vs keyword run |
|---|---|---|
| Matswale murder | pass: 1 record, Crime & Security, Musina | better |
| Neltox SANDF shooting | caught: 1 record, Africa & World, no place | better |
| Carrim | not a pass: 11 records, 3 Crime & Security, 6 Africa & World, 2 Priority Intelligence; Musina on 1 record | same |
| Cigarette seizure | pass: 1 record, Priority Intelligence, Musina | better |
| Low water notice | pass: 1 record, Infrastructure, Musina | better |
| Musina protest noise (keep out) | pass: no Durban or Tunisia protest saved | better |

Noise: Google News saved 266 rows against the keyword run's 330, and Civic Intelligence saved 117 against the 1,859 the other three runs saved. Civic dropped 703 items as not about Musina. Of the 383 rows, 343 don't name Musina's area: most are Google News results that name no place (Google returns loosely related stories for the town words) and local-paper leads whose headlines name no place.

A first run on the code before the robbery word and the tighter local-paper filter gave the same bench result (5 caught, 3 full passes, keep-out 1 of 1) with 392 rows saved.

**Caveat:** Matswale and Neltox are searched by name because the gazetteer top-up got them from the Musina sweep itself. That makes Musina an optimistic test for those two cases. The Malamulele check below has no such help.

## Malamulele (second town, manual comparison)

Town mode run: 28 searches, 209 seconds, 153 rows saved, 19 about Malamulele. Manual search: news and web searches for Malamulele, 1 to 23 October 2026.

| Found by manual search | Town mode |
|---|---|
| Comedian Skeem Mafenya stabbed to death, 17 Oct, three arrested the next day | Caught, Crime & Security, Malamulele on the main records (one placed in Johannesburg). But 17 records with 6 main records, not one story. |
| Armed gang disrupts a study session at Nkatini High School, Xigalo, tablets and phones stolen (Limpopo Mirror, 21 Oct) | Missed in the first run. No search used "robbery". With robbery added, caught ("Police Hunt Armed Gang After Overnight Study Session Disrupted at Malamulele School"), but filed in Africa & World with no place. |
| Residents fear a "Water Mafia" (News24, 2 Oct) | Caught, Africa & World, no place. |
| Masingita Mall land approval questions (14 Oct) | Caught, Africa & World. Not an incident. |
| Malamulele Stadium closed by the PSL (15 Oct) | Caught, Africa & World. Not an incident. |

Town mode also found two municipal water notices the manual search didn't (Malamulele Water Treatment Works interruption, 9 Oct, Infrastructure; Gondeni Ha-Mavhunga restoration, 10 Oct). A mob justice story from Mphakati village came up in a search summary, but its source dates it to September 2025, so it is out of the window and not counted.

Verdict: town mode finds what the manual search finds in Malamulele once robbery is a search word. It does not file it as well: two of the three crime items are in the wrong stream or unplaced, and the Mafenya murder is split across six main records.

## What town mode doesn't fix

- **Stream for stories without crime words in the headline:** the Nkatini robbery ("armed gang ... disrupted") and Neltox ("army crackdown") go to Africa & World. That is Batch 2's classifier, not search.
- **One story, one record:** Mafenya (6 main records) and Carrim (11) still split. That is Batch 5's merge.
- **Noise from loose Google News results:** most saved rows name no place, so the area check can't drop them.
- **Local-paper leads:** headline only, so most Limpopo Mirror and Zoutpansberger leads (sport, arts) are kept in every Vhembe town run.
- **Towns not in the gazetteer** (Elim, for one) are refused.

## Replay bench

The full replay bench is unchanged from Batch 7: 9 of 22 full passes, 17 caught, keep-out 7 of 10 (`collector_bench_batch8_2026-10-23.json`). Town mode's changes only act when a run is in town mode or searches live, so replay can't see them. Tests: 863 passed, 4 skipped.

## Reports

The run reports are in this folder: `batch8_town_mode_musina_report.md` and `batch8_town_mode_malamulele_report.md`.
