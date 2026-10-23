# Batch 7 live check, 23 Oct 2026 (code b20b5e2)

The replay bench can't score a new source: it replays rows the sweeps saved, and no sweep saved a sitemap row. So Batch 7 was checked with live runs on copies of the sweep databases:

- `--town Musina --local-only --from 2026-10-01` on a copy of the Musina sweep's civic.db
- `--town "Louis Trichardt" --local-only --from 2026-10-01` on a copy of the three-town sweep's civic.db
- `--town Newcastle --local-only`: reads nothing, and says the Newcastillian and Northern Natal News are blocked

Each run made six requests (two robots.txt, four sitemaps), at least 10 seconds apart per site. No article page was requested.

## What came in (each run)

- 43,980 article links across the four sitemaps; 161 changed on or after 1 October; 119 leads after joining stories both papers carry (17 leads have both links).
- Headlines: 23 from the news sitemaps' own titles, 96 rebuilt from the article link.
- Streams: 14 Crime & Security (10 priority), 105 Africa & World. Before the Vhembe/Makhado fix in this batch the first run put 20 in Crime & Security, six of them only because the headline named Vhembe.
- Places: 6 of 119 leads got a place from the headline. The rest carry the paper's coverage area as `lead_area`, which is not a place.

## The three cases every collector missed

| Case | Caught | Stream | Place | One record | Date | Bench pass |
|---|---|---|---|---|---|---|
| lt_mashawana_bodies | yes, the case's own link | Crime & Security, unresolved death (not a killing) | Mashawana (Thohoyandou precinct) | yes | n/a | yes |
| lt_botlokwa_explosives | yes ("Two arrested with explosives after uber driver raises alarm") | Crime & Security | none: the headline doesn't say Botlokwa | yes | 5 Oct, matches | no (place) |
| musina_neltox_sandf | yes, the case's own link, both papers joined | Africa & World: the headline doesn't mention the shooting | none: the headline doesn't say Neltox | yes | 15 Oct (page last changed), incident 11 Oct | no |

Neltox, Mashawana, the Matswale murder and the Madombidzha death were each flagged as repeating an existing pin (`repeats_pin`), so a reviewer sees they're already on the map.

## Problems seen, not fixed here

- Waterval: "Three arrested months after Waterval filling station robbery" was placed at the Waterval near Mokopane. The paper covers Vhembe, where there's another Waterval. A place rule that prefers the paper's own area would fix it (Batch 3/4 place logic).
- "Family seeks answers after missing granny's body is found" was flagged as repeating the Madombidzha pin on headline likeness (0.66). The Madombidzha pin is a man of 53, so this is probably a wrong match.
- "Content creator threatens legal action over shooting report" went to Crime & Security for review: the word "shooting" alone.
- Headlines rebuilt from links lose apostrophes and capitals for places the gazetteer doesn't know ("makumeke").
