# User Guide

AI JobPilot helps you find jobs, see how well you fit, prepare your documents and track applications. It prepares
everything for you; **you** approve each application before it is sent.

Open the dashboard at http://127.0.0.1:8502 (see `DEPLOYMENT.md` to start it).

## First-time setup (about 15 minutes)

1. **Resume Manager** - upload your resume (PDF or DOCX). Check the extracted profile, correct anything wrong, add
   skills you genuinely have, then confirm the review. Nothing is invented: fields the parser could not find stay
   empty and are listed as missing.
2. **Settings -> Preferences** - job titles, locations, work mode, salary, notice period. Empty preferences leave
   checks such as location as "Unknown".
3. **Companies** - list the companies to watch in `config/career_sources.yaml` (Greenhouse or Lever boards).
4. Optional: set `ADMIN_TOKEN` in `.env` so the buttons that change things (approve, scheduler, settings) work.
   Without it the dashboard is read-only.

## Daily use

| Page | What you do there |
|---|---|
| **Dashboard** | See counts, pending actions, upcoming interviews and system status. |
| **Job Discovery** | Run discovery, filter jobs, import a job by hand, build a LinkedIn search link. |
| **Job Matching** | See the score, the eligibility result and the reasons for each job; paste any job text to score it. |
| **Document Generation** | Create a tailored resume, cover letter and screening answers; edit; approve. |
| **Application Queue** | Review prepared applications; approve, reject, pause or hand off. |
| **Applied Jobs** | Search past applications, add notes, record outcomes. |
| **Interview Tracker** | Add and update interviews. |
| **Analytics** | Rates and source performance over a date range; CSV export. |
| **Automation** | Start/stop the daily scheduler, see runs and limits. |
| **Settings** | Preferences, scoring weights, limits, status of integrations. |

## Finding jobs

- **Company boards (automatic):** Job Discovery -> run discovery. Reads only the companies you listed, through
  their public job-board API.
- **LinkedIn, Indeed, Naukri (manual):** these sites do not allow automated reading. Use *LinkedIn (manual)* on the
  Job Discovery page: build a search link, open it in your own browser, then paste the job link and its text into
  the import form. The app stores it; it never visits LinkedIn itself.

## Reading a match

Two separate results:

- **Match score (0-100):** how closely your profile fits the job.
- **Eligibility (Pass / Fail / Unknown):** hard requirements such as mandatory skills, minimum experience and
  location. *Unknown* means the data to decide is missing; it is never treated as a pass.

A mandatory skill counts only when it is written in your reviewed profile. If you have a skill that is missing,
add it in Resume Manager and score again.

## Applying

1. Generate and approve the documents for a job.
2. The application is prepared (form answers mapped from your profile).
3. In **Application Queue**, review it and press **Approve**. Nothing is submitted before this.
4. For sites where automation is not permitted (the default for every real site), the app gives you a **manual
   handoff**: the prepared answers and documents, for you to submit yourself. Record the result in Applied Jobs.

Automatic form filling works today only on local test forms. To allow it for a real Greenhouse, Lever or company
site, you must check that site's terms and list it under `authorized_sites` in
`config/application_policies.yaml`. No real site has been tested.

## Daily automation

After the setup in `DEPLOYMENT.md` section 4, the scheduler runs every day: discover -> score -> queue good matches
-> prepare. Then it waits for your approval. Limits in `config/automation_limits.yaml` are hard caps.

## Common questions

- **No jobs found** - add companies to `config/career_sources.yaml`; loosen the title filter.
- **Everything is "Fail"** - open the reasons. Usually minimum experience, or mandatory skills not written in your
  profile.
- **Buttons are disabled** - `ADMIN_TOKEN` is not set, or the profile is not reviewed.
- **"Unknown" location** - fill locations and relocation preference in Settings.
- **Is an AI key required?** - No. Scores and eligibility are rule-based. A key only enables optional wording help.
- **Can it apply on LinkedIn for me?** - No. LinkedIn, Indeed and Naukri prohibit it; the app gives you a manual
  handoff instead.
