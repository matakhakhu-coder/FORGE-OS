# Town mode run: Musina (2026-10-23T20:36:55Z)

- Window: from 2026-10-01 (SAST) to the end of the run. Database: `/home/sprite/forge-batch8-live/final/musina-final.db`. Took 210.0 s.
- Searches: 33, all with when:24d. Places searched from the gazetteer: Matswale (collector gazetteer top-up), Musina-Nancefield (GeoNames section), Neltox (collector gazetteer top-up), Tshamutumbu (collector gazetteer top-up).
- Local papers read: Limpopo Mirror, Zoutpansberger. Not read: none.
- Civic Intelligence feeds left out (switched-off publishers): citizen_news, review_online, letaba_herald. Civic items dropped as not about Musina: 703.
- Saved: 383 rows, 36 about Musina (8 of those placed), 347 not.
  - civic: 117 saved, 8 about the town, streams {'CRIME_INTEL': 2, 'GLOBAL': 5, 'INFRASTRUCTURE': 1}
  - kw: 266 saved, 28 about the town, streams {'CRIME_INTEL': 4, 'GLOBAL': 20, 'PRIORITY': 4}

## Bench (musina, scored on what this run saved)

Town mode: 5 of 5 caught, full pass 3, keep-out 1 of 1.
23 Oct keyword run (saved): 3 caught, full pass 0, keep-out 0 of 1; 330 rows saved, 32 naming the area.
23 Oct all four runs (saved): 4 caught, full pass 1, keep-out 0 of 1; 2189 rows saved, 42 naming the area.
today's code replayed over 23 Oct inputs: 4 caught, full pass 3, keep-out 1 of 1; 2189 rows saved, 42 naming the area.

| Case | Records | Streams | Places | Pass | vs keyword run | vs all runs | vs replay |
|---|---|---|---|---|---|---|---|
| musina_matswale_murder | 1 | {'CRIME_INTEL': 1} | {'Musina': 2} | yes | better | better | same |
| musina_neltox_sandf | 1 | {'GLOBAL': 1} | {'none': 2} | no | better | better | better |
| musina_carrim | 11 | {'CRIME_INTEL': 3, 'GLOBAL': 6, 'PRIORITY': 2} | {'Musina': 1, 'none': 34} | no | same | same | same |
| musina_cigarette_seizure | 1 | {'PRIORITY': 1} | {'Musina': 3, 'none': 2} | yes | better | better | same |
| musina_low_water_notice | 1 | {'INFRASTRUCTURE': 1} | {'Musina': 1} | yes | better | same | same |
| musina_protest_noise | 0 | - | - | pass | better | better | same |

Noise (bench area words): 383 saved, 40 naming the area, 343 not.

## Rows about Musina

| Collector | Stream | Place | Date | Title |
|---|---|---|---|---|
| kw | CRIME_INTEL | - | 2026-10-14 | R10 Million Fine For Zimbabwean Driver Caught With Cigarettes At Beitbridge - iHarare News |
| kw | CRIME_INTEL | Musina | 2026-10-21 | R1.5m illicit cigarette haul seized in Musina as suspect faces court - https://novanews.co.za/ |
| civic | CRIME_INTEL | - | 2026-10-21 | Suliman Carrim faces additional charges after Malawi-bound cars intercepted at Beitbridge - News24 |
| kw | CRIME_INTEL | Musina | 2026-10-22 | Musina businessman shot more than 20 times in store - Limpopo Mirror |
| kw | CRIME_INTEL | - | 2026-10-22 | Suliman Carrim Now Faces Car Theft Charges, To Appear In Musina Court - The Bulrushes |
| civic | CRIME_INTEL | Musina | 2026-10-22 | Musina businessman shot more than 20 times in store |
| kw | GLOBAL | - | 2026-10-02 | Minister Nguluvhe calls for collective action in Beitbridge clean-up - Herald.co.zw |
| kw | GLOBAL | - | 2026-10-06 | Beitbridge cement, fertiliser plant to anchor MATABELELAND South industrialisation drive - Herald.co.zw |
| kw | GLOBAL | - | 2026-10-07 | Musina residents warned as elephants roam near town centre - Limpopo Mirror |
| kw | GLOBAL | - | 2026-10-07 | Musina residents warned as elephants roam near town centre - Zoutpansberger |
| kw | GLOBAL | - | 2026-10-07 | 6 stray elephants cause safety scare in Musina - LNN - Network News - The Citizen |
| kw | GLOBAL | - | 2026-10-07 | Warning: 6 stray elephants spotted in Musina - The Citizen |
| kw | GLOBAL | - | 2026-10-07 | Beitbridge Border Post: Anchor for COMESA principles, model for SADC, and AU trade facilitation - Herald.co.zw |
| civic | GLOBAL | - | 2026-10-07 | Musina residents warned as elephants roam near town centre |
| civic | GLOBAL | - | 2026-10-08 | Amanti expands their services to Musina farmers |
| civic | GLOBAL | - | 2026-10-08 | Mapungubwe festival promises to be bigger and better than ever |
| kw | GLOBAL | - | 2026-10-13 | Beitbridge restoration blitz targets illegal businesses - Herald.co.zw |
| kw | GLOBAL | - | 2026-10-15 | Zimbabwean sentenced for R10.4m illicit cigarette haul through Beitbridge - https://novanews.co.za/ |
| kw | GLOBAL | - | 2026-10-16 | Transporters roped in to curb child smuggling at Beitbridge border - Herald.co.zw |
| kw | GLOBAL | - | 2026-10-16 | Omalayitsha roped in to fight child smuggling at Beitbridge - Herald.co.zw |
| kw | GLOBAL | - | 2026-10-20 | More buses deployed as thousands await repatriation at Musina border - weekendargus.co.za |
| kw | GLOBAL | - | 2026-10-20 | Government not funding food for migrants at Musina centre, Home Affairs official says - weekendargus.co.za |
| kw | GLOBAL | - | 2026-10-20 | Musina landfill site leads Limpopo audit with 82% compliance score - Limpopo Mirror |
| civic | GLOBAL | - | 2026-10-20 | Musina landfill site leads limpopo audit with 82 compliance score |
| kw | GLOBAL | - | 2026-10-21 | Suliman Carrim faces additional charges after Malawi-bound cars intercepted at Beitbridge - News24 |
| kw | GLOBAL | - | 2026-10-22 | LIVE / Carrim, co-accused appear in Musina Magistrates’ Court - SABC News |
| kw | GLOBAL | - | 2026-10-22 | Businessman Suliman Carrim appears in the Musina Magistrate’s Court - SABC News |
| kw | GLOBAL | - | 2026-10-22 | WATCH: Suliman Carrim Appears In Musina Court For Car Theft, Fraud, Uttering - The Bulrushes |
| kw | GLOBAL | - | 2026-10-22 | Sulliman Carrim in Musina court over attempted theft of Absa vehicles - The Citizen |
| kw | GLOBAL | - | 2026-10-22 | Four Arrested in Beitbridge and Plumtree Over Vehicle Smuggling - iHarare News |
| civic | GLOBAL | - | 2026-10-23 | 913 Arrested In Beitbridge - ZimEye |
| civic | INFRASTRUCTURE | Musina | 2026-10-11 | LOW WATER SUPPLY IN MUSINA TOWNSHIP AND SURROUNDING AREAS |
| kw | PRIORITY | Musina | 2026-10-21 | R1.5m illicit cigarette haul seized in Musina as alleged syndicate leader escapes - IOL |
| kw | PRIORITY | Musina | 2026-10-21 | R1.5m illicit cigarette haul seized in Musina as alleged syndicate leader escapes - Joburg ETC |
| kw | PRIORITY | Musina | 2026-10-22 | Suliman Carrim faces Musina court over alleged Absa vehicle theft and money laundering - Joburg ETC |
| kw | PRIORITY | Musina | 2026-10-23 | SANDF recovers three stolen vehicles and smuggled cigarettes during Musina border operations - Joburg ETC |
