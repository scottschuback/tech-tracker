# Setting up the Technology Tracker: step by step

Time: about 40 minutes once. Steps 1 to 3 need a computer (to upload the folder). Everything after that works from your phone.
You will end up with: an app at your own web address, a daily email, and a Claude Code routine that writes the analyst notes.

---

## Step 1. Create a GitHub account (5 min)
GitHub is a free site that stores the code and hosts the app.
1. Go to github.com and choose Sign up. Use the email you want the tracker to be tied to.
2. When it offers two-factor sign-in, turn it on.

## Step 2. Create the repository and upload the project (10 min, computer)
A "repository" is just a folder on GitHub.
1. Signed in, tap the + at the top right, then New repository.
2. Repository name: `tech-tracker`. Choose Public (free app hosting needs this; it holds only public filing data and your notes). Leave everything else unticked. Tap Create repository.
3. On your computer, unzip `tech-tracker.zip`. Open the unzipped `tech-tracker` folder.
   - On a Mac, press Cmd+Shift+. in that folder so the hidden `.github` folder shows. On Windows it shows by default.
4. Back on the repository page, tap Add file, then Upload files.
5. Drag everything inside the `tech-tracker` folder (including `.github`, `config`, `docs`, `tracker`, `tests` and the files) into the upload box. Wait for the list to finish.
6. Tap Commit changes at the bottom.
7. Check: the repository page should show folders named `.github`, `config`, `docs`, `tracker`, `tests`.

Easier if you use Claude Code on a computer: open the unzipped folder and say "Create a public GitHub repository called tech-tracker from this folder and push it."

## Step 3. Turn on the app (3 min)
1. Repository page, tap Settings (the tab on the right).
2. Left menu, tap Pages.
3. Under Build and deployment, Source: Deploy from a branch. Branch: `main`, folder: `/docs`. Tap Save.
4. Wait a minute and refresh. It shows your app address, like `https://YOUR-NAME.github.io/tech-tracker/`. Copy it.
5. Still in Settings, left menu: Secrets and variables, then Actions. Tap the Variables tab, then New repository variable. Name `APP_URL`, value = the address you copied. Add it.

The app will show "Could not load data.json" until the first run in Step 7. That is normal.

## Step 4. Add the secrets (5 min)
Secrets are private settings the daily run uses. Settings, Secrets and variables, Actions, Secrets tab, New repository secret. Add these one at a time (names exactly as written):

| Name | What to put |
|---|---|
| `TRACKER_USER_AGENT` | `Scott TechTracker your@email.com` (the SEC requires a name and email on every request) |
| `GMAIL_USER` | the Gmail address that will send the email |
| `GMAIL_APP_PASSWORD` | the 16-letter app password from Step 5 |
| `EMAIL_TO` | the address the email goes to (can be the same Gmail) |
| `PATENTSVIEW_API_KEY` | the free patent key from Step 6 (add later if it has not arrived) |

## Step 5. Gmail app password (5 min)
Gmail will not let a program use your normal password, so you create a one-off one.
1. On your phone or computer go to myaccount.google.com, then Security.
2. Turn on 2-Step Verification if it is off.
3. In the search box at the top of the account page type "App passwords" and open it.
4. Name it `tech tracker` and tap Create. Copy the 16 letters (spaces do not matter) into the `GMAIL_APP_PASSWORD` secret.

## Step 6. Free patent key (2 min, then wait)
1. Go to patentsview.org and find the API key request form (search the site for "API key").
2. Request a key. It arrives by email, sometimes after a day or two.
3. When it arrives, add it as the `PATENTSVIEW_API_KEY` secret. Until then the Patents source shows "failed: no key" and everything else runs normally.

## Step 7. First run (15 to 20 minutes of waiting)
1. Repository page, tap the Actions tab.
2. Left side, tap Daily tracker run.
3. Right side, tap Run workflow. Tick backfill (it seeds the alert scoreboard from past filings). Tap the green Run workflow button.
4. A new run appears; tap it to watch. Green tick = done. Red cross = tap it, open the failed step, and paste the error to me.
5. Open your app address. The Home tab should show every company by layer with its verdict.
6. Check your email. The first one lists everything from the last two weeks, so it is longer than usual.

From now on it runs by itself every morning at 7am AEDT (6am AEST). The next step is optional but worth it.

## Step 8. The analyst (Claude Code routine, 5 min)
This is the part that reads each red and amber item at its source and writes the plain-English notes you see on the Analyst notes tab.
1. Open Claude Code (desktop, or the Code tab in the Claude app). Connect it to GitHub and to Gmail when it asks.
2. Open the Routines section and create a new routine.
3. Schedule: daily at 7:45am your time (45 minutes after the data run).
4. Instructions: paste the text from `ROUTINE_PROMPT.md` in the project.
5. Save. The routine runs in the cloud, so your computer can be off. You can see each run from the Claude mobile app.

## What you will see each day
- Email at about 7am: movers first (candidates to price, never buy signals), then red and amber items, then any failed sources, with a link to the app.
- A second short email only if something new is red.
- The app: Home (verdicts by layer), Today, Movers, Layers, Analyst notes, Last 30 days, Promises, Scoreboard, Number tests, What's watched.

## Colours and words
- Verdicts: Strong, Watch, Cycle low, Weak (rules on each company card; backtested 2014 to 2021).
- Alerts: red = read today, amber = check, green = news, grey = summarised.
- Sources: green = new items, grey = checked and nothing new, red = failed (reason shown), violet = planned, not built.
- Tags: F filed with a regulator, C company said, R reported and unverified.

## If something goes wrong
- The run fails on "Test the number checks": the code tests failed; paste the log to me.
- The email did not arrive: check the three Gmail secrets, and that 2-Step Verification is on. The run log says "Email not sent" if a secret is missing.
- Prices show failed: the free price site may block GitHub's servers at times; it retries the next day and nothing else depends on it.
- A company is missing: its ticker was not found at the SEC; the app lists missing tickers on the What's watched tab.

## Changing things later
- Companies and layers: `config/layers.yaml`. Grades: `config/grades.yaml`. Promises: `config/promises.yaml`. Guidance: `config/guidance.yaml`. Alert colours: `config/rules.yaml`.
- Run time: the `cron` line in `.github/workflows/daily.yml` (in UTC; 20:00 UTC = 7am AEDT).
- Or just tell Claude Code: "add Arm's new promise", "move KLA to the equipment layer", "run the tracker now".
