# Source register

Every statement in `medication_facts.md` cites one or more of these sources by ID.
All sources are public consumer health information from government services. Nothing here
is copied from a product label or a company website, and brand names that appear on some
source pages are not reproduced.

| ID | Title | URL | Page's own review date | Accessed |
|----|-------|-----|------------------------|----------|
| S1 | Semaglutide Injection — MedlinePlus Drug Information (US National Library of Medicine) | https://medlineplus.gov/druginfo/meds/a618008.html | Last revised 2026-05-15 | 2026-10-08 |
| S2 | Semaglutide — NHS Medicines A to Z | https://www.nhs.uk/medicines/semaglutide/ | Last reviewed 2026-05-15 | 2026-10-08 |
| S3 | Physical activity guidelines for adults aged 19 to 64 — NHS | https://www.nhs.uk/live-well/exercise/physical-activity-guidelines-for-adults-aged-19-to-64/ | Last reviewed 2024-05-22 | 2026-10-08 |
| S4 | Eating a balanced diet — NHS | https://www.nhs.uk/live-well/eat-well/how-to-eat-a-balanced-diet/eating-a-balanced-diet/ | Last reviewed 2026-06-30 | 2026-10-08 |
| S5 | Weight loss medicine — healthdirect Australia | https://www.healthdirect.gov.au/weight-loss-medicine | Last reviewed 2026-03 | 2026-10-08 |
| S6 | Loss of muscle mass (sarcopenia) — healthdirect Australia | https://www.healthdirect.gov.au/loss-of-muscle-mass | Last reviewed 2026-02 | 2026-10-08 |
| S7 | Poisoning — healthdirect Australia | https://www.healthdirect.gov.au/poisoning | Last reviewed 2023-11 | 2026-10-08 |

## Known conflicts between sources

- **Missed doses.** S1 gives two rules: within 5 days when the medicine is used for
  diabetes, and within 2 days (48 hours) when it is used for weight loss. S2 gives only the
  5-day rule, on a page that covers both uses. The clinic follows S1's weight-loss rule because
  it is the one written for this use. See `docs/decisions.md` (D3).
- **Strength training.** S3 says strengthening activities on at least 2 days a week; S6 says
  resistance training 3 times a week in the context of preventing muscle loss. F32 keeps the
  general S3 figure and F33 mentions resistance training without a number, so the sheet never
  holds two different rules for one question.

Both readings were checked against the source text on 2026-10-08.
