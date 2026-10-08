# Source register

Every statement in `medication_facts.md` cites one or more of these sources by ID.
All sources are public consumer medicine information. Nothing here is copied from
a product label or a company website.

| ID | Title | URL | Page's own review date | Accessed |
|----|-------|-----|------------------------|----------|
| S1 | Semaglutide Injection — MedlinePlus Drug Information (US National Library of Medicine) | https://medlineplus.gov/druginfo/meds/a618008.html | Last revised 2026-05-15 | 2026-10-08 |
| S2 | Semaglutide — NHS Medicines A to Z | https://www.nhs.uk/medicines/semaglutide/ | Last reviewed 2026-05-15 | 2026-10-08 |
| S3 | Physical activity guidelines for adults aged 19 to 64 — NHS | https://www.nhs.uk/live-well/exercise/physical-activity-guidelines-for-adults-aged-19-to-64/ | Last reviewed 2024-05-22 | 2026-10-08 |
| S4 | Eating a balanced diet — NHS | https://www.nhs.uk/live-well/eat-well/how-to-eat-a-balanced-diet/eating-a-balanced-diet/ | Last reviewed 2026-06-30 | 2026-10-08 |

## Known conflict between sources

S1 and S2 give different missed-dose windows (S1: within 2 days for the weight-management
product; S2: within 5 days, a page that also covers the diabetes products). The clinic
follows S1 because S1 describes the weight-management use specifically. See
`docs/decisions.md` (D3). `TODO-JUSTIN: verify` this reading of the two pages.
