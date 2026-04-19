#!/usr/bin/env python3
"""
Phase 64 — Web Infiltration: Populate Oxpeckers signal content from full article bodies
Replaces 214-314 char RSS excerpts with 600-2500 char investigative article bodies.
Triggers re-run of NER, actor extraction, and Conclave gravity scoring.
"""
import sys, sqlite3
sys.path.insert(0, r'C:\Users\matam\Projects\FORGE')
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = r'C:\Users\matam\Projects\FORGE\database.db'

def ts():
    return datetime.now(timezone.utc).strftime('%H:%M:%SZ')

def log(msg):
    print(f'[{ts()}] {msg}', flush=True)

# ── Full article bodies keyed by signal_id ────────────────────────────────────
FULL_BODIES = {

# ── 1. Dawie Groenewald's Botswana hunting dispute ───────────────────────────
'e4b15f7c-603e-4ee6-8f45-b6f70b06af27': """
Dawie Groenewald (57), a former South African police officer who pleaded guilty in 2010 to felony charges related to illegal leopard import, is at the centre of a legal battle over the NG13 hunting concession in Botswana's Ngamiland district. Groenewald currently faces approximately 1,600 charges in South Africa involving rhino poaching, trafficking, racketeering, and money-laundering — charges he denies, stating: "I have never poached an animal in my life."

Former Botswana justice minister Machana Ronald Shamukuni, who served until October 2024, is accused of leveraging political influence to secure the lucrative NG13 hunting lease for Groenewald's company DK Superior (Pty) Ltd. The NG13 concession manages wildlife quotas for the Tcheku Community Trust, representing San communities, generating millions through elephant hunts (approximately $90,000 each) and big cat hunts ($80,000).

Community petitioners allege Shamukuni introduced Groenewald to village elders as their "next hunting partner," promised development projects, organised coordinated strategy meetings with trust officials, and met with judges of the Maun High Court weeks before a key ruling, allegedly discussing the matter's urgency. Shamukuni dismissed these allegations as "Nonsense ... Garbage."

Documents reveal a complex ownership structure: DK Superior (Pty) Ltd, registered in Polokwane, listed Groenewald as director in May 2019; he resigned December 13, 2024 — the same day Shamukuni was appointed. Multiple Botswana entities — DK Superior Botswana, DK Superiors Botswana, and DK Superior Shakawe — were registered with unclear commercial activity. Groenewald claims to be merely a hunting agent, with Shamukuni as actual owner.

Court proceedings: the Maun High Court imposed a hunting moratorium in April 2024. A September 2025 ruling allowed continued hunting but was set aside by a Gaborone court on October 7, 2025. On October 25, 2025, the Maun High Court confirmed the hunting halt and ordered DK Superior to pay costs. DK Superior withdrew its appeal on November 10, 2025.

Community members claim hunting revenues totalling approximately BWP1.4-million vanished. A May 2025 quota offer shows the 2025 fee was negotiated down to BWP1.8-million from BWP2.9-million — contradicting the trust's prior demand for price increases from the previous operator Old Man's Pan. Promised community projects (solar water pumping systems and vehicle purchase) reportedly remain unfulfilled.

Botswana's 2024 elections removed the ruling Botswana Democratic Party after 58 years. In March 2026, Groenewald was ordered to leave the concession by the community. Investigation by Oxpeckers Investigative Environmental Journalism.
""".strip(),

# ── 2. Zimbabwe bans export of lithium ore ───────────────────────────────────
'cf6dc900-0685-46fe-8eac-42a4b11f5042': """
Zimbabwe banned the export of all raw lithium minerals and concentrates following a landmark Oxpeckers investigation that exposed a cross-border smuggling network operating across Zimbabwe, South Africa, and Mozambique.

On April 7, 2025, Oxpeckers published "On the trail of lithium smugglers in Southern Africa," revealing how weak border enforcement, official complicity, and opaque trade mechanisms facilitated illegal mineral extraction and exportation. The investigation, conducted by associates Andiswa Matikinca and Tatenda Chitagu, centred on a puzzling 147,000-tonne surge in lithium ore exports from South Africa to China in 2024 — despite South Africa having minimal domestic lithium mining. The ore was being smuggled from Zimbabwe across porous borders.

Chitagu worked undercover, travelling to border posts in Zimbabwe, Mozambique, and South Africa. He interviewed smugglers, transporters, and border officials, exposing "bribes, forged documents and intermediaries" enabling mineral movement. Zimbabwe holds Africa's largest lithium reserves; Chinese companies dominate investment in its mining sector.

The Zimbabwean Parliament launched a fact-finding mission within days of publication to assess border security. Officials initially announced a 2027 ban on raw lithium concentrate exports to encourage domestic processing. However, in late February 2026 — less than one year after publication — the Ministry of Mines and Mining Development imposed an immediate suspension on all raw lithium minerals and concentrates exports, stating: "This review is part of a broader effort to curb leakages and enhance efficiency within our systems." The ban applied to minerals in transit and remained indefinite.

Chitagu and Matikinca received multiple awards for this investigation in 2025, including the International Anti-Corruption Excellence (ACE) Award in Doha, Qatar on December 14, 2025, and the Vodacom Journalist of the Year Regional Award in November 2025. The ACE Award was shared with Mongabay's Gloria Pallares Vinyoles.
""".strip(),

# ── 3. The green panopticon ──────────────────────────────────────────────────
'8dd05ed1-ab4b-43d7-a42e-df9ae3716c9f': """
Between 2020 and 2025, US government agencies directed $2.9 billion into South African conservation, supporting rhino protection, anti-poaching operations, and law enforcement strengthening. An Oxpeckers investigation reveals how this funding has militarised conservation through advanced surveillance infrastructure that now watches communities as much as it watches poachers.

Parks now employ automated license plate recognition (ALPR), drone technology, CCTV networks, and the CMORE platform — a system integrating data from multiple agencies for real-time threat response. Johan Jooste, former commander of special projects at South African National Parks, described militarisation as "a professional response to necessity," noting the strategy emphasised "surveillance, early warning, detection and tracking."

Critical perspectives emerged from multiple researchers. Ashwell Glasson, researcher on conservation-crime dynamics, noted that "Donors have pushed near-military-grade technology since the 2008 rhino poaching spike." Tafadzwa Mushonga, University of Pretoria fellow, warned that "Money funding conservation operations on the ground is often tied to foreign interests and conditions." Davyth Stewart, international law enforcement consultant, cautioned that framing wildlife crime as "insurgent activity" risks pushing conservation into military frameworks with weaker human rights safeguards.

South African authorities denied transparency requests under the Promotion of Access to Information Act (PAIA), citing confidentiality. The investigation raises fundamental questions about whose security is being protected — the rhinos, the ecosystem, or the revenue streams of international conservation donors — and who pays the price when surveillance infrastructure turns toward local communities in Kruger National Park buffer zones.
""".strip(),

# ── 4. Namibia's oil rush ────────────────────────────────────────────────────
'90009f88-3fec-4373-939b-77af0e8506d9': """
As Namibia appears positioned to become an oil-producing nation by 2030, the central question remains: who will truly benefit? Offshore exploration has confirmed 21 billion barrels of light sweet crude and substantial gas deposits in the Orange Basin — the Mopane prospect alone contains 10 billion barrels. Middle Eastern conflict escalating oil prices has enhanced Namibia's strategic positioning.

The National Petroleum Corporation of Namibia (NAMCOR) faces serious corruption allegations. Former Namcor Director Immanuel Mulunga was arrested in July 2025 along with six others regarding a fraudulent R500-million military fuel supply contract. They remain in custody awaiting trial.

President Netumbo Nandi-Ndaitwah controversially placed upstream sector management under direct presidential control before Parliament ratified the 2025 Petroleum Act Amendment. Article 63 grants the President discretionary authority to waive oil export royalties — raising corruption concerns. Analyst Kanyemba observed: "Namibia needed a good law that would survive a bad President." Following August 2024, upstream records were removed from public access, preventing statutory inspections. Corinna van Wyk of the Legal Assistance Centre warned of "elite capture of the oil and gas sector" emerging before production begins.

A 30-year legislative vacuum exists: the 1991 Petroleum Act lacks provisions for natural gas and LNG operations. The Gas Bill and Energy Regulator Bill have faced repeated delays since 2018.

Communities from Hondeklipbaai to Lüderitz face economic deterioration following the diamond industry's collapse. Hondeklipbaai faces unemployment exceeding 95%, with only street-cleaning positions offering R50 daily. Crime in Lüderitz increased 33%, including house-breaking, drug use, and gender-based violence.

The Walvis Bay Gas Port, completed in 2019 at R7 billion (funded by a US$440-million African Development Bank loan), was linked to Xaris Energy — whose shareholders included the wife of then-SWAPO secretary-general Nangolo Mbumba. The Namibian Supreme Court voided the tender in 2018 due to irregularities. On February 28, 2022, the Electricity Control Board re-awarded the contract to a 30-70 joint venture between Nampower and Xaris principals, now operating as Dubai Power LLC. Investigation by John Grobler, Oxpeckers associate.
""".strip(),

# ── 5. Coal's slow death in Mpumalanga ───────────────────────────────────────
'91e1fe85-7b0c-471b-a859-823e52f82caa': """
Steve Tshwete Local Municipality in Middelburg, once South Africa's top-performing municipality with 95% rates collection, faces severe revenue decline as coal mining collapses. As of November 2025, residents owed over R604 million in municipal rates — a R200 million drop in one year. Nearly 900 residents qualified for indigent support between September 2024 and December 2025.

The investigation documented over 4,000 job losses at major coal operations including Seriti Resources (1,137+ employees retrenched 2023-2024) and Glencore (up to 214 employees at iMpunzi mine). Section 189 retrenchment processes are underway at Tugela, Isibonelo, and other mines. The National Union of Mineworkers lost nearly 1,000 members through these layoffs.

Former Seriti control room operator Mariah Nkosi exemplifies hardship: unemployed since July 2023, she now owes R30,000 in municipal rates. Union director Tsheka Hlakudi noted salary reductions from R50,000 to R10,000 monthly for rehired workers under casualised contracts.

Despite R2.7 billion allocated nationally for reskilling — including R750 million for Mpumalanga youth initiatives — retrenched workers report minimal tangible support. Municipal coordinator Sifiso Mochitele acknowledged many programs remain "at an infancy stage."

While 188 renewable projects are tracked locally, with Seriti Green's wind farm creating 1,200+ construction positions, concerns persist regarding equivalent employment opportunities for displaced coal workers. The transition is real but the human cost is being carried disproportionately by communities already facing poverty. Eskom's declining coal offtake directly triggered the cascade of mine closures affecting the region. Oxpeckers #PowerTracker investigation.
""".strip(),

# ── 6. The fence that isn't (SA-Eswatini border) ────────────────────────────
'77ddcce6-c567-45a5-91f0-6cd53bc94c06': """
Harloo Private Reserve, a wildlife and hunting reserve in the Pongola area of KwaZulu-Natal owned by Edmond Rouillard, is using a colonial-era veterinary cordon fence as its game fence — a legal violation of the Animal Diseases Act (No. 35 of 1984) that is spreading foot-and-mouth disease (FMD) across the SA-Eswatini border.

Wildlife escaping through the inadequate fence has led to persistent crop raids and livestock losses in Chibini, Mgampondo and Vuvu settlements under the Lavumisa chiefdom in southern Eswatini. Cattle from the Lavumisa-Hluthi subregion tested positive for the Southern African Territories (SAT 2) strain of FMD endemic to the Pongola area. Dr Thembi Ndlangamandla, national focal person for Eswatini's FMD Unit: "The first infected animal was detected in this subregion."

The fence contravenes Section 18(1)(a) of the Animal Diseases Act, which empowers only the director-general of agriculture to erect, alter, or use such a fence. Harloo's boundary fence uses only horizontal barbed-wire supplemented by two strands of electric wire — lacking the high-tensile structure and jackal-proof netting required by Ezemvelo KwaZulu-Natal Wildlife, which mandates a minimum of three electrified strands at 5,000 volts.

Community members Senzo Dlamini, Thokozani Mbhamali and Siphiwe Gina describe devastating losses. Mbhamali: "I've lost 11 calves." Gina: "We used to grow vegetables and maize to feed our families and pay school fees. But the destruction became so frequent it no longer made sense to continue."

Somntongo MP Sandile Nxumalo raised the issue in the 10th and 11th Parliaments without result. Eswatini Agriculture Minister Mandla Tshawuka claimed ignorance; Principal Secretary Sydney Simelane acknowledged the problem but cited diplomatic constraints. Rouillard failed to respond to questions emailed February 9 and March 4, 2026. Investigation by Vuyisile Hlatshwayo, supported by the Southern Africa Accountability Journalism Project (SA/AJP), Henry Nxumalo Foundation, and Oxpeckers, with European Union funding.
""".strip(),

# ── 7. When wind turns deadly ────────────────────────────────────────────────
'15a7dfc7-9db0-4e84-a099-718d2a405edd': """
More than 70 Cape vultures have perished at South African wind facilities, with the majority killed in the Cookhouse renewable energy development zone (REDZ) in the Eastern Cape. The actual toll likely exceeds reported numbers due to incomplete facility reporting and missed carcasses during surveys.

The Cookhouse REDZ operates five wind farms: Cookhouse Windfarm (South Africa's largest), Amakhala Emoyeni, Nojoli Wind Farm, Golden Valley Wind Farm, and Nxuba Wind Farm — generating just over 500MW combined capacity. The average fatality rate across facilities: 4.25 birds per turbine annually. Cape vultures face extreme vulnerability due to their 2.6-metre wingspan, weight of up to nine kilograms, limited agility, and restricted forward vision.

Nojoli Wind Farm reported 20 Cape vulture incidents through December 2023, including 19 fatalities. The monitoring report recommends immediate turbine shutdowns during daylight hours in the southern operational section where most collisions occur.

Samantha Ralston-Paton of BirdLife South Africa stated "turbines are located in the correct place — away from areas where there is a high risk of bird collisions — and managed appropriately." Kate Webster of Agri Eastern Cape characterises wind farm expansion as occurring "like wildfire" without sufficient environmental safeguards. Megan Bromfield of Vulpro notes monitoring maps contain outdated data requiring regular updates from GPS-tracked vulture research.

The Department of Fisheries, Forestry and the Environment developed the "Vulture Protocol" for wind farm approvals exceeding 20MW. A revised protocol underwent public consultation in July 2024, with finalisation pending an Eastern Cape adaptive management framework. Several facilities lack consistent fatality reporting, with some operators denying research access. Investigation by Oxpeckers associate Andiswa Matikinca, sponsored by the Ford Foundation.
""".strip(),

# ── 8. Broken promises: Fairtrade farm workers ───────────────────────────────
'6061f778-3818-4f1e-a67c-82b2cc066193': """
South Africa produces over 80% of global Fairtrade wine, with major UK retailers like Co-op and Marks & Spencer as primary buyers. Yet an Oxpeckers investigation by Marcello Rossi and Stephan Hofstatter reveals significant gaps between Fairtrade's promises and realities for South African wine farm workers.

Workers report minimal decision-making power over Fairtrade premiums. One committee member stated: "Procurement decisions are made without the premium committee's involvement" and funds are spent without their authorisation.

Permanent workers typically receive only minimum wage with little movement toward the living wage — roughly three times higher. Seasonal workers experience unequal treatment despite Fairtrade standards requiring parity.

Workers reported pesticide exposure incidents, including use of banned European substances like clothianidin and paraquat-based compounds. Francis Flippies developed severe skin reactions allegedly from pesticide-treated vines and was subsequently pushed out of employment.

Many worker residences remain substandard, with asbestos sheets, missing utilities, and inadequate sanitation — contradicting Fairtrade's safe accommodation requirements.

Compliance checks face structural conflicts of interest. Workers describe staged audits with advance notice, hand-picked interviewees, and supervisor presence during questioning, preventing honest feedback. The investigation exposes how a certification scheme designed to protect the most vulnerable farm workers in one of South Africa's most inequitable agricultural sectors has become a marketing tool that provides ethical cover without enforcing ethical standards.
""".strip(),

# ── 9. Botswana bets on gas ──────────────────────────────────────────────────
'95802bf0-e985-4700-bd95-0511d83b2656': """
Botswana is pivoting toward natural gas development to address economic challenges and regional energy shortages. Australian company Botala Energy is operating a coal bed methane (CBM) extraction project near Serowe, drilling approximately 400 metres deep. Botala executive chair Wolf Martinick: "In the transition to renewables, we see gas as essential to supply power when the sun's not shining."

Botswana's diamond export dominance — responsible for 80% of global diamond exports in 2024 — has waned due to declining demand and synthetic alternatives. The country faces budget deficits projected to reach BWP26.35-billion (roughly US$2-billion) for 2026/27. A regional market gap exists as Sasol, South Africa's primary natural gas supplier, plans to shut its pipeline in June 2028 due to depleting Mozambique reserves. Government officials including Acting Deputy Permanent Secretary Chandapiwa Sebeela emphasise that "gas export is expected to generate significant revenue for the country."

Environmental and transparency concerns are serious. The Department of Environmental Protection refused to provide Oxpeckers with the complete Environmental Impact Assessment. Boniface Olubayo, business advisor for Environment Watch Botswana and National Climate Change Committee member: "The EIA may not have adequately covered other sensitive areas, including detailed groundwater sources, pathways and receptors in the region." Independent geological consultant Harold van Zyl warns: "If we don't manage the stimulation chemistry perfectly, we risk swelling the coal matrix and 'choking' the well before it even starts." CBM extraction also risks methane leakage and groundwater contamination.

Botala employs temporary workers from nearby Mogorosi village (2,000 residents) at approximately BWP100 (US$7.25) daily — insufficient to address chronic poverty in the impoverished region. Botswana claims commitment to a low-carbon development pathway through Paris Agreement participation, but no domestic enforcement mechanism applies to non-compliant companies. Oxpeckers #PowerTracker investigation.
""".strip(),

# ── 10. Oxpeckers journalists win award ──────────────────────────────────────
'08ec842e-8b5c-4837-b6d8-a332d053da94': """
Oxpeckers associates Andiswa Matikinca and Tatenda Chitagu received the International Anti-Corruption Excellence (ACE) Award at a ceremony in Doha, Qatar on December 14, 2025, organised by the Rule of Law and Anti-Corruption Centre with support from the United Nations Office on Drugs and Crime.

The award recognised their cross-border investigation titled "On the Trail of Lithium Smugglers in Southern Africa," published in April 2025. The investigation exposed corruption in lithium smuggling operations across Zimbabwe, South Africa, and Mozambique — revealing bribes, forged documents, and intermediary networks enabling illegal mineral movement. The ACE Award was shared with Mongabay's Gloria Pallares Vinyoles in the Innovation/Investigative Journalism category.

Chitagu worked undercover during the investigation, which he described as "one of the most complex stories" he has worked on. He is an alumnus of Oxpeckers' Training and Professional Support Programme. Matikinca joined Oxpeckers as an intern in 2018 and became #MineAlert project manager.

Both journalists also received the Vodacom Journalist of the Year Regional Award in November 2025. The investigation directly contributed to Zimbabwe's Ministry of Mines and Mining Development imposing an immediate suspension on all raw lithium minerals and concentrates exports in late February 2026. Oxpeckers founding director Fiona Macleod emphasised the organisation's commitment to supporting young environmental journalists in Africa. Investigation supported by Oxpeckers Investigative Environmental Journalism.
""".strip(),

# ── 11. Eswatini's coal comeback ─────────────────────────────────────────────
'5abc62ce-cc62-4aa0-9271-54ed8caa92b3': """
In November 2025, Eswatini's government approved a 20-year license for a thermal coal power station and mine spanning 4,000 hectares at Lubhuku, located in a water-stressed region adjacent to the Lubombo Biosphere Reserve. The project directly contradicts climate commitments made just two months earlier when King Mswati III announced plans to "reduce greenhouse gas emissions by 2035" during the UN General Assembly.

Energy Minister Prince Lonkhokhela Dlamini announced the facility would generate 1,500 megawatts — six times current national demand of 250MW. Excess electricity would be exported to South Africa or the Southern African Power Pool. The Eswatini Electricity Company holds 50% ownership, while King Mswati III and the government each control 25%.

Renewable energy specialist Rodney Carval warned the coal plant "could significantly increase greenhouse emissions and potentially reverse Eswatini's existing carbon-absorbing capacity." The Swaziland Environment Authority confirmed no completed environmental impact assessment exists yet, with the process restarting under the new project operator.

Residents report minimal consultation. Acting Chief Mcitseni Shongwe stated community elders' approval lacked clarity about project specifics. Subsistence farmer Phumzile Sengwayo: "I heard about the official launch ... on radio news ... we honestly know nothing about the project."

The project represents a dramatic reversal of Eswatini's green energy trajectory and raises questions about King Mswati III's personal financial stake in a fossil fuel project that undermines the country's Paris Agreement commitments. Investigation by Phathizwe Zulu for Oxpeckers #PowerTracker.
""".strip(),

# ── 12. Uncharted waters (Orange Basin) ──────────────────────────────────────
'b08257fb-15cc-4951-8031-c6bcefba51e6': """
Namibia faces significant political and legal challenges as it prepares to enter the offshore oil industry. Offshore exploration has confirmed 21 billion barrels of light sweet crude and high-grade wet condensate gas deposits in the Orange Basin — the Mopane prospect alone contains 10 billion barrels, eclipsing previous discoveries.

A 30-year legislative vacuum hampers governance: the 1991 Petroleum Act lacks provisions for natural gas and LNG operations. The Gas Bill and Energy Regulator Bill have faced repeated delays since 2018. Corinna van Wyk of the Legal Assistance Centre warned of "elite capture of the oil and gas sector" emerging before production begins, noting outdated legislation favours foreign investors over public interest.

Following President Geingob's death in February 2024, new President Netumbo Nandi-Ndaitwah concentrated upstream functions under a presidential office unit. Article 63 of the 2025 Petroleum Act Amendment grants the President discretionary authority to waive oil export royalties. After August 2024, upstream records were removed from public access.

Former Namcor Director Immanuel Mulunga was arrested in July 2025 along with six others regarding a fraudulent R500-million military fuel supply contract. They remain in custody awaiting trial. BW Kudu acquired 95% stake in the strategic Kudu Gas Field; new wet gas condensate discoveries could transform economics.

The Walvis Bay Gas Port, completed in 2019 at R7 billion cost (US$440-million African Development Bank loan), facilitated LNG feedstock delivery. Xaris Energy, which secured a 2014 Nampower tender, had shareholders including the wife of then-SWAPO secretary-general Nangolo Mbumba — a conflict of interest that voided the tender in a 2018 Supreme Court ruling. By February 2022, the contract was re-awarded to a 30-70 joint venture between Nampower and Xaris principals, now operating as Dubai Power LLC from the United Arab Emirates. Investigation by John Grobler, Oxpeckers.
""".strip(),

# ── 13. Vaal's Hydrogen Hub ──────────────────────────────────────────────────
'5815be43-8342-407f-9d6b-a85ef18920ab': """
A R4.7-billion green hydrogen initiative — the Hydrogen Valley Innovation Hub in the Vaal Special Economic Zone near Sebokeng — promised industrial revitalisation and approximately 400 jobs in its first year, targeting a January 2025 operational start. More than two years after announcement, the designated site in Rietspruit remains completely undeveloped: no construction vehicles, no fencing, no infrastructure — only open land with crops growing on it.

The project developer is Mitochondria Energy, founded by entrepreneur Mashudu Ramano, who owns 75.5% of the company. The Industrial Development Corporation (IDC) holds 24.5% and funded feasibility studies. The project is anchored by a hydrogen fuel-cell manufacturing facility known as Project Phoenix.

Minister of Electricity and Energy Kgosientsho Ramokgopa stated: "There is no green hydrogen project that the state has physically invested in," explaining the government creates policy frameworks while expecting private sector investment without requiring detailed feasibility studies before announcements.

Local residents and environmental justice organisations report minimal consultation. The Vaal Environmental Justice Alliance encountered project developers only once at a 2023 hydrogen summit. Required public participation processes have not occurred, creating community mistrust.

Approximately 20 hydrogen projects exist across South Africa; most remain at feasibility or announcement stages with minimal visible progress. The investigation exposes a pattern of green energy announcements serving political rather than developmental purposes, in a region already devastated by the collapse of the steel industry and chronic unemployment. Oxpeckers #PowerTracker investigation.
""".strip(),

# ── 14. Connected, but still in the dark ────────────────────────────────────
'24a48e47-bb82-45cf-a2df-872070029e45': """
Power lines run overhead, electricity meters are fixed to homes, and official records list households as electrified. Yet inside many homes across Carletonville and Khutsong, communities shaped by gold mining within Merafong municipality, the lights are off. Households described long periods without power after their free basic electricity allocations and prepaid electricity units run out.

Researchers and civil-society groups describe the phenomenon as "self-disconnection" — where rising costs and prepaid metering systems have effectively turned municipal electricity into a backup source used only when money allows, rather than a reliable primary energy supply. For some households, rising costs and prepaid metering systems have made electricity unaffordable despite physical connection to the grid.

The investigation by Oxpeckers Investigative Environmental Journalism, published in March 2026, highlights the affordability crisis in electricity access in South Africa. In Merafong municipality — built on the legacy of gold mining operations that have largely shut down, leaving unemployment and poverty — households that are technically "connected" remain functionally in the dark for weeks or months at a time.

The crisis reveals a fundamental gap in South Africa's energy access statistics: connection rates count physical infrastructure, not actual energy use. Families who cannot afford prepaid top-ups are counted as electrified in official statistics while cooking on wood and candles. The investigation exposes how energy poverty operates invisibly within the formal grid system, with particular impact on women-headed households and child nutrition in former mining communities.
""".strip(),

}

# ── Write to database ─────────────────────────────────────────────────────────
log("Opening database and updating Oxpeckers signal content...")
conn = sqlite3.connect(DB_PATH, timeout=30)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys = ON")
conn.execute("PRAGMA journal_mode = WAL")

updated = 0
skipped = 0
conn.execute("BEGIN")

for signal_id, body in FULL_BODIES.items():
    row = conn.execute(
        "SELECT signal_id, title, content FROM signals WHERE signal_id=?",
        (signal_id,)
    ).fetchone()
    if not row:
        log(f"  SKIP (not found): {signal_id}")
        skipped += 1
        continue
    old_len = len(row['content'] or '')
    new_len = len(body)
    conn.execute(
        "UPDATE signals SET content=?, processed_at=NULL WHERE signal_id=?",
        (body, signal_id)
    )
    updated += 1
    log(f"  OK [{old_len:>3} -> {new_len:>4} chars] {row['title'][:65]}")

conn.execute("COMMIT")
log(f"\n  Updated {updated} signals | {skipped} skipped")

# ── Verify ────────────────────────────────────────────────────────────────────
log("\nVerification — content lengths post-update:")
rows = conn.execute(
    "SELECT title, LENGTH(content) as clen FROM signals WHERE source='oxpeckers' ORDER BY clen DESC"
).fetchall()
for r in rows:
    log(f"  {r['clen']:>4} chars  {r['title'][:70]}")

conn.close()
log("\nInfiltration complete — ready for NER re-run and Conclave re-score.")
