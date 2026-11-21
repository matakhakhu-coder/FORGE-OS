# FORGE Map sourcing rule

This is the rule for which reports can support a FORGE Map pin. It judges each report on its own sourcing, not on who published it. It replaces the earlier editorial rule "Caxton papers are not sources".

- **Approved:** by the owner on a voice call, 21 November 2026, about 16:00 SAST. The approved wording: "stop focusing on who published the story and just judge the report itself ... require traceable sourcing and independent corroboration of the core incident, with copied reports counting as just one source. That way, a local paper can contribute evidence without its report being enough to publish on its own."
- **Scope, settled the same day:** the owner chose that corroboration is required when the only reporting comes from local papers. It is not required for every pin.
- **In force for:** candidates checked from 21 November 2026. Pins already on the map are not re-judged by this rule, and nothing is withdrawn because of it.

This rule is about evidence. It does not approve publishing anything. Every pin still needs an owner approval record in `docs/approvals/pins`, and every other safeguard below still applies.

## The rule

1. **Judge the report, not the publisher.** No outlet is excluded as a source because of who owns it. Caxton titles (for example Review Online, Letaba Herald, Northglen News, Bloemfontein Courant, The Citizen) are judged like any other report.
2. **Every report used must be traceable.** It must say where its account comes from: police or another named official, a named spokesperson, a court record, a named witness or family member, or the reporter's own presence. A report that gives no source for the core incident can be a lead, but it cannot support a pin.
3. **Local papers alone are not enough.** When every report of the core incident comes from local or community papers, the pin needs independent corroboration before it can be approved. A local paper can be one of the sources on a pin, and can add detail, but it cannot carry a pin on its own.
4. **Corroboration has to be independent.** It counts only when it comes from a separate account of the core incident (that it happened, roughly when, and where), such as:
   - an official statement read at its source (SAPS newsroom or SAPS social media, a municipality, a court, a ministry), or
   - a report from a different outlet that has its own sourcing for the core incident, not one copied from the local paper.
5. **Copies count once.** Syndicated articles, the same article on a sister site, rewrites of another outlet's report, and several reports all resting on the same single statement are one account, not corroboration. The existing independence labels (`single_report`, `same_outlet`, `same_statement`, `shared_wording`, `partly_independent`) still describe the reporting on each pin.
6. **Other reports stand as before.** A traceable report from an outlet that is not a local paper (a national or provincial outlet, a metro daily, the SABC or a radio newsroom, an official statement) can still support a pin on its own, labelled `single_report`, as it could before this rule.

## What counts as a local paper

A local or community paper covers one town, district or set of suburbs, usually as a weekly or free community title, in print or online. Examples: Review Online, Limpopo Mirror, Zoutpansberger, Letaba Herald, Lowvelder, TygerBurger, Athlone News, Northglen News, Mossel Bay Advertiser, Newcastillian, Limpopo Chronicle.

Metro dailies (for example Daily Voice, Cape Argus, Cape Times), national outlets, the SABC and radio newsrooms are not local papers for this rule. When it is unclear whether a title is a local paper, treat it as one.

## Example

The Polokwane town-centre stabbing (Polokwane Review, 12 November 2026) was held in the 21 November sweep only because Caxton papers were not sources. Under this rule the Polokwane Review report can count, but it is a local paper, so the pin also needs an independent account, such as a SAPS statement or another outlet with its own sourcing. Until one is found, it stays held, now for lack of corroboration.

## What has not changed

- **Machine reading.** Collectors still never fetch Caxton sites, and the Newcastillian and other outlets stay unread by machine unless written permission is recorded in `data/article_reader_permissions.json` (see `data/town_sources.json`). Caxton's terms (clause 2.7.1) ban automated monitoring or copying without written authority. This rule changes what a report counts for, not how FORGE may collect it.
- **Reading by a person.** A Caxton article that turns up in a search may be read on its public page, as any reader would, linked and cited, with its facts in FORGE's own words and short quotes only. Do not routinely browse Caxton listing pages to monitor them: clause 2.7.1 also mentions a "manual process to monitor", so that stays off. This is a risk-reduction choice, not legal advice.
- **Every other check.** Original articles read in full, not search snippets. Incident date kept apart from report date. Each key fact matched to a quote from a named source. Attribution of allegations and police accounts. Arrests are not convictions. Naming rules. Location precision and the 'approx.' and 'ESTIMATED MAP POINT' labels. Places that cannot be put on the map are held. Duplicate checks. Stream rules and owner precedents.
- **Approval.** An owner approval record is still required for every pin, and the build still refuses a pin without one. This rule does not create a standing approval, does not turn on `FORGE_ALLOW_PUBLISH`, and does not change any schedule or automation.
