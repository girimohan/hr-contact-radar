# HR Contact Radar

Finds job openings in Finland, the Nordics and remote-in-Europe, and **the person you can write to about each
one**, so you can send your CV and portfolio straight to a recruiter or hiring manager instead of only filling in
the form. A direct note also reaches teams that are hiring for roles they have not advertised.

Sister project of `remote-job-radar` (same idea: collect, match, score, dashboard), but the output is people,
not just postings.

## What you get

| | |
|---|---|
| **Dashboard** | `private/dashboard.html`, one file, opens with a double-click. Openings ranked by how good a contact they have and how well they fit you. For each: the contacts, how each was found, a ready email draft (English, or Finnish for Finnish postings), *Open in my email app* / *Open in Gmail*, and a tracker (emailed, replied, interview, do not contact). A **Follow-ups** tab lists people you emailed 7+ days ago who have not replied. |
| **Digest** | `private/digest.md`: today's new openings with a contact. |
| **Data** | `private/leads.json`: everything shown in the dashboard. |

Nothing is ever sent for you. You review each draft, attach your CV and press send yourself.

## How a contact is found, best first

1. **Named in the posting's own data.** Arbetsförmedlingen (Sweden) and NAV (Norway) ads have a contact-person
   field; HR-manager feeds name the hiring project leader; Teamtailor job pages name the recruiter.
2. **Named in the posting text.** `hrradar/extract.py` reads the contact block that Nordic ads end with
   ("Lisätietoja tehtävästä antaa…", "Mer information lämnas av…", "Spørsmål om stillingen rettes til…",
   "Questions? Reach out to…") and pulls out name, title, phone and email, including `name(at)firma.fi`-style
   addresses. When the ad says "sähköpostit muotoa etunimi.sukunimi@firma.fi", the person's address is built
   from that and marked as built.
3. **The posting's application address** (`rekry@`, `careers@`, a Recruitee per-job inbox, an HN poster's email).
4. **The company's website.** When nobody is named, `hrradar/enrich.py` finds the company's domain, reads its
   careers / contact / team pages (robots.txt respected, at most 6 pages, cached 14 days) and keeps recruiters,
   founders / CTOs / heads of engineering, and recruiting mailboxes. People in sales or support are ignored.
5. **A guessed address**, only for a named person with no address: the company's own format if its site shows
   one, otherwise `firstname.lastname@` (most common in the Nordics, ä→a, ö→o). Always labelled
   *address guessed*, and the dashboard asks you to check it before sending. Every domain gets an MX check
   (does it accept mail at all?) over DNS-over-HTTPS; only the domain name is sent.

## Sources

| Source | Country | Contact data | How |
|---|---|---|---|
| Arbetsförmedlingen JobSearch | Sweden | contact person in about half the tech ads | official API, no key |
| NAV stilling-feed | Norway | contact list per ad | official feed, public token |
| HR-manager (Talentech) | Nordics | hiring project leader with email | public feed per company |
| Teamtailor | Nordics | recruiter's name on the job page | public RSS + job page per company |
| Laura (rekrytointi.com) | Finland | parsed from the ad text | public RSS per company |
| Varbi | Sweden | parsed from the ad text | public RSS per employer |
| Recruitee | any | the job's application inbox | public API per company |
| Greenhouse, Lever, Ashby, SmartRecruiters | any | parsed from text, then company website | public APIs; ~600 companies shared with remote-job-radar, Nordic and remote-EU postings only |
| Hacker News "Who is hiring?" | remote | the poster's own email | Algolia HN API |

**Not used, on purpose:** Duunitori (its robots.txt disallows all automated access), Oikotie (closed), LinkedIn
(terms), Jobnet.dk (no open API). **Työmarkkinatori** is the best Finnish source (most ads carry a structured
recruiter contact), but its internal API is disallowed in robots.txt, so it is not collected. The official way in
is its free open interface, which needs an activation form with a business ID; once you have credentials it can
be added as a source.

### Add companies

Finnish and Nordic employers on these ATS platforms are the biggest lever. Put their slugs in
`data/companies.json`:

```json
{
  "teamtailor": ["acme", "career.acme.fi"],
  "laura": ["acme"],
  "hrmanager": ["acme"],
  "recruitee": ["acme"],
  "varbi": ["kth"]
}
```

How to spot them: a careers page at `acme.teamtailor.com` or with "Powered by Teamtailor"; `acme.rekrytointi.com`
(Laura); links to `candidate.hr-manager.net/...` (HR-manager); `acme.recruitee.com`; `acme.varbi.com`.
Greenhouse, Lever, Ashby and SmartRecruiters slugs go in the same file (or in remote-job-radar's).

## Set up and run

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
python -m hrradar run --open                      # full run, about 10 minutes the first time
python -m hrradar run --sources jobtech,nav       # quick: Sweden and Norway only
python -m hrradar run --no-sites                  # skip reading company websites
```

1. Fill in `profile` in `config.yaml`: name, portfolio link, LinkedIn, and a two-sentence pitch. The drafts
   use them. Edit `outreach` to change the wording.
2. Tune `roles`, `exclude_title` and `profile.skills` the same way as in remote-job-radar.
3. Run it every day: on GitHub (next section) or with Windows Task Scheduler.

## Live on GitHub (Actions + Pages, encrypted)

`.github/workflows/daily.yml` runs the radar twice a day on GitHub and publishes the dashboard to
`https://<you>.github.io/hr-contact-radar/`. The output is people's names and addresses, so on GitHub it only
ever exists encrypted (AES-256-GCM, key from your passphrase with PBKDF2, 600,000 rounds):

- **The page** is a lock screen; your browser decrypts the dashboard after you type the passphrase. Tick
  *Remember on this device* on your own computer and phone only.
- **The state** the next run needs (NAV ads, website contacts, first-seen dates) is saved encrypted in the
  Actions cache.
- **The issue** GitHub emails you after each run lists new jobs only, never contacts.

Set up once:

```powershell
gh secret set RADAR_PASSPHRASE          # 12+ characters; it opens the page, so pick a long one and keep it
gh workflow run daily.yml               # first run now instead of waiting for the morning
```

The repository is public (free GitHub Pages need that), so keep your own email and phone out of
`config.yaml`. Put them in a second secret instead; it's merged over `profile`:

```powershell
gh secret set RADAR_PROFILE --body "email: you@example.com`nphone: '+358 40 000 0000'"
```

Your *Contacted* tracker lives in each browser's storage, so it isn't shared between your laptop and phone;
use *Export* / *Import* in the dashboard to move it. Changing the passphrase starts the saved state fresh.
The local run (below) keeps working the same way.

Run it on your own computer instead, or as well, with Windows Task Scheduler:

```powershell
schtasks /Create /SC DAILY /ST 07:30 /TN "HR Contact Radar" /TR "cmd /c cd /d %USERPROFILE%\Desktop\projects\hr-contact-radar && python -m hrradar run"
```

## Good practice (and GDPR)

- Recruiters' names and work emails are personal data even when the employer published them. Using them for
  your own job search is fine; the radar keeps only what the posting or company site published, stores it in
  `private/` (git-ignored), and drops a contact when its posting disappears. NAV's terms require that, and the
  NAV store deletes inactive ads on every run.
- One personal message per opening, and usually one per company: the dashboard hides companies you already
  wrote to and warns before a second message.
- Respect "apply only through the form" and anyone who asks not to be contacted (*Do not contact again*).
- Check every *address guessed* before sending. Never mass-send templates; edit the draft so it is about them.
- Every request identifies the tool, runs once a day, respects robots.txt on company sites and backs off when
  rate-limited.

## Layout

```
hrradar/
  sources/      jobtech, nav, hrmanager, teamtailor, recruitee, laura, varbi (nordic.py); ats.py; hackernews.py
  extract.py    contact people from posting text (FI / SV / NO / DA / EN)
  enrich.py     company domain, website crawl, address format, MX check
  match.py      role, place (Finland / Nordics / remote-EU), language, score
  pipeline.py   collect -> select -> contacts -> enrich -> score -> private/
  secure.py     encryption for the published page and the saved state
  dashboard_template.html
data/           companies.json, plus caches with no personal data (domains, mx, seen, NAV cursor)
private/        leads, dashboard, website and NAV caches: personal data, never committed
```
