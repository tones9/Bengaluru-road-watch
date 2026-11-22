# Road Watch · South Bengaluru

Tap any road in South Bengaluru and see **who is responsible for fixing it**: the agency that maintains it, the engineers' office for that ward, the corporation commissioner, the MLA, and the corporator (none elected since 2020). Residents add dated photo reports, and anyone can record a candidate's promise, with a source.

- **Map:** roads from OpenStreetMap, tagged with the GBA ward they fall in (2025 delimitation, 72 wards of Bengaluru South City Corporation).
- **Responsibility chain:** `data/officials.json`, with a source for every name. Road ownership is marked *likely* when it is worked out from the road type rather than an official register.
- **Reports and promises** are GitHub issues (templates in `.github/ISSUE_TEMPLATE`). A workflow collects them into `data/reports.json` every 3 hours and whenever an issue changes. Label an issue `fixed` when the repair is confirmed, or `verified` once a promise's source has been checked.
- **Hosting:** GitHub Pages, built by `.github/workflows/build.yml`.

Data: © OpenStreetMap contributors (ODbL). Ward boundaries: OpenCity / GBA, via kaun.city (MIT).
