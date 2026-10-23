# Collector bench scorecard (replay, code 087f81e)

- Catch cases: 17 of 22 caught; right stream on every record 10; a right place 13; one record 8; a right date 7; full pass 4.
- Keep-out cases: 2 of 10 pass (1 not testable on this data).
- Not scored: catch cases with a suggested precinct on any record 13; records flagged as an arrest or court place 17.

| Case | Kind | Records | Streams | Places | Precincts (not scored) | Stream | Place | One record | Date | Pass |
|---|---|---|---|---|---|---|---|---|---|---|
| musina_matswale_murder | catch | 1 | CRIME_INTEL 1 | Musina 1 | Musina 1 | all | all | yes | none | yes |
| musina_neltox_sandf | catch | 0 | - | - | - | n/a | n/a | - | none | no |
| musina_carrim | catch | 45 | CRIME_INTEL 9, GLOBAL 23, PRIORITY 13 | none 45 | none 45; arrest/court place flagged 8 | some | none | no | some | no |
| musina_cigarette_seizure | catch | 8 | CRIME_INTEL 2, GLOBAL 2, PRIORITY 4 | Musina 6, none 2 | Musina 6, none 2 | some | some | no | none | no |
| musina_low_water_notice | catch | 1 | INFRASTRUCTURE 1 | Musina 1 | Musina 1 | all | all | yes | all | yes |
| lt_phiphidi_killing | catch | 1 | CRIME_INTEL 1 | Phiphidi 1 | Thohoyandou 1 | all | all | yes | none | yes |
| lt_univen_shooting | catch | 1 | CRIME_INTEL 1 | none 1 | none 1 | all | none | yes | n/a | no |
| lt_madombidzha_death | catch | 1 | CRIME_INTEL 1 | Madombidzha 1 | Tshilwavhusiku 1 | all | all | yes | n/a | yes |
| lt_mashawana_bodies | catch | 0 | - | - | - | n/a | n/a | - | none | no |
| lt_botlokwa_explosives | catch | 0 | - | - | - | n/a | n/a | - | none | no |
| plk_shandesh_deaths | catch | 2 | CRIME_INTEL 2 | Polokwane 1, none 1 | Polokwane 1, none 1 | all | some | no | n/a | no |
| plk_golf_club_body | catch | 3 | CRIME_INTEL 3 | Polokwane 3 | Polokwane 3 | all | all | no | all | no |
| plk_cricket_club_death | catch | 2 | CRIME_INTEL 2 | Polokwane 2 | Polokwane 2 | all | all | no | n/a | no |
| plk_airport_officers | catch | 17 | CRIME_INTEL 9, GLOBAL 7, PRIORITY 1 | Polokwane 10, none 7 | Polokwane 10, none 7; arrest/court place flagged 7 | some | some | no | some | no |
| plk_tvet_official | catch | 7 | PRIORITY 7 | Polokwane 1, none 6 | Polokwane 1, none 6 | all | some | no | all | no |
| plk_seshego_school_illness | catch | 3 | CRIME_INTEL 1, GLOBAL 2 | Seshego 1, none 2 | Seshego 1, none 2 | some | some | no | n/a | no |
| nc_newcastle_west_water | catch | 8 | GLOBAL 2, INFRASTRUCTURE 6 | Newcastle 6, none 2 | Newcastle 6, none 2 | some | some | no | some | no |
| nc_signal_hill | catch | 0 | - | - | - | n/a | n/a | - | none | no |
| nc_ncandu_park | catch | 0 | - | - | - | n/a | n/a | - | none | no |
| nc_power_cut | catch | 1 | INFRASTRUCTURE 1 | none 1 | none 1 | all | none | yes | all | no |
| nc_p1_counterfeit | catch | 1 | CRIME_INTEL 1 | Newcastle 1 | Newcastle 1 | none | all | yes | n/a | no |
| nc_p2_quad_bike | catch | 1 | CRIME_INTEL 1 | none 1 | none 1 | none | none | yes | none | no |
| nc_au_sydney_syndicate | keep out (not_placed_in_province) | 1 | PRIORITY 1 | Newcastle 1 | Newcastle 1 | - | - | - | - | fail |
| nc_au_mater | keep out (not_placed_in_province) | 2 | GLOBAL 1, PRIORITY 1 | Newcastle 2 | Newcastle 2 | - | - | - | - | fail |
| nc_au_stockton | keep out (not_placed_in_province) | 2 | INFRASTRUCTURE 2 | Newcastle 1, none 1 | Newcastle 1, none 1 | - | - | - | - | fail |
| musina_protest_noise | keep out (absent) | 7 | GLOBAL 7 | Durban 5, none 2 | Durban Central 5, none 2 | - | - | - | - | fail |
| lt_nrcs_feed_date | keep out (date_is_incident) | 0 | - | - | - | - | - | - | - | not_tested |
| lt_r524_crash | keep out (not_stream) | 9 | GLOBAL 9 | Levubu 1, Louis Trichardt 2, none 6 | Levubu 1, Makhado 2, none 6 | - | - | no | - | pass |
| plk_sekhukhune_officers | keep out (not_placed_at) | 2 | PRIORITY 2 | none 2 | none 2; arrest/court place flagged 2 | - | - | - | - | pass |
| nc_oosthuizen_court | keep out (court_step_flagged) | 1 | CRIME_INTEL 1 | none 1 | none 1 | - | - | - | - | fail |
| nc_mlambo_court | keep out (court_step_flagged) | 1 | CRIME_INTEL 1 | none 1 | none 1 | - | - | - | - | fail |
| plk_88_firearms | keep out (court_step_flagged) | 1 | PRIORITY 1 | none 1 | none 1 | - | - | - | - | fail |

Replay fidelity: 404 of 434 rows got the same stream as saved, 165 the same place; 0 replay errors.
