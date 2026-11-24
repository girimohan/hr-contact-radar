# Project notes

Short working notes for picking this up later. The README has the full user guide.

## Owner

Mohan Giri, Machine Learning Engineer and M.Sc. student (CS, ML & AI) at the University of Helsinki, looking for
ML / AI engineering roles in Finland (Helsinki area or remote).
Portfolio https://girimohan.vercel.app/ · GitHub [girimohan](https://github.com/girimohan) ·
LinkedIn [girimohan8](https://www.linkedin.com/in/girimohan8/). Email and phone live only in the `RADAR_PROFILE`
secret, because this repo is public.

## What it is

Finds Finnish, Nordic and remote-in-Europe openings **plus the person to email about each one**
(recruiter, hiring manager, founder/CTO, or a recruiting inbox), with a ready draft per lead. Nothing is sent
automatically. Sister project: `../remote-job-radar` (finds remote jobs open to Finland; shares
`data/companies.json`).

## Where it stands (2026-11-24)

- v1 built and committed (`1e15396`); live version on GitHub Actions + Pages (`a27ad9f`).
- Repo https://github.com/girimohan/hr-contact-radar, dashboard https://girimohan.github.io/hr-contact-radar/
  (passphrase-locked). Runs daily 04:41 UTC, backup 11:05 UTC; a GitHub issue lists new jobs (no contacts).
- First live runs: ~33,000 postings read → 1,310 kept, 411 with someone to write to, 171 with a named person.
- 27 tests pass (`python -m pytest -q`).

## Stack

- **Python 3.12**: `requests`, `PyYAML`, `cryptography`; `pytest` for tests. No framework, no database: JSON files.
- **Sources**: Arbetsförmedlingen JobTech (SE), NAV stilling-feed (NO), HR-manager, Teamtailor, Laura, Varbi,
  Recruitee, Greenhouse, Lever, Ashby, SmartRecruiters, Hacker News "Who is hiring?" (Algolia API).
- **Contact finding**: posting fields → regex extraction from FI/SV/NO/DA/EN ad text → application inbox →
  company website crawl (robots.txt respected) → guessed `firstname.lastname@` (labelled) → MX check over DNS-over-HTTPS.
- **Dashboard**: one self-contained HTML file (vanilla JS), tracker in `localStorage`, Export/Import.
- **Hosting**: GitHub Actions (cron + manual), Actions cache for state, GitHub Pages, `gh issue create` for alerts.
- **Privacy**: AES-256-GCM, key from PBKDF2-SHA256 (600k rounds); decrypted in the browser with WebCrypto.
  Contacts never appear in clear text on GitHub; locally they stay in git-ignored `private/`.

## Rules we kept to

- Contacts are personal data (GDPR): store only what was published, drop when the posting disappears.
- Respect robots.txt: **Duunitori** (disallows all) and **Työmarkkinatori**'s internal API (`/api/` disallowed)
  are not collected. LinkedIn is out (terms). Oikotie closed, Jobnet.dk has no open API.
- The user sets secrets (`RADAR_PASSPHRASE`, `RADAR_PROFILE`) themselves.

## Ideas / next

- **Finnish coverage is the weak spot** (~87 of ~1,300 leads). Biggest lever: add Finnish employers' Teamtailor,
  Laura, HR-manager and Recruitee slugs to `data/companies.json` (hrmanager, laura, recruitee lists are empty now).
- Työmarkkinatori open interface (official, needs a business ID; Barmo's could work) as a proper source.
- Sync the *Contacted* tracker across devices (today it is per browser).
- Add `cv_url` once a public CV link exists.
- Workflow actions run on deprecated Node 20 (GitHub forces Node 24, still works); bump versions when v5/v6 land.

## Commands

```powershell
python -m hrradar run --open                 # local full run, opens private/dashboard.html
python -m hrradar run --sources jobtech,nav  # quick
gh workflow run daily.yml -R girimohan/hr-contact-radar
gh run list -R girimohan/hr-contact-radar --limit 3
```
