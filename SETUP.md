# People & Purpose — Daily Briefing

Automatically generates a daily meeting briefing at **5:00 PM Mountain Time** the evening before, pulling live data from Google Calendar and Gmail via Claude.

---

## Repo structure

```
index.html                          ← generated each evening (served by GitHub Pages)
scripts/
  generate.py                       ← generation script
.github/
  workflows/
    generate-briefing.yml           ← GitHub Actions schedule
```

---

## One-time setup

### 1. Create the GitHub repo

1. Go to [github.com/new](https://github.com/new)
2. Name it `people-briefing` (or anything you like)
3. Set visibility to **Private** (recommended — your calendar data will be in commits)
4. Create the repo, then upload these files maintaining the folder structure above

### 2. Enable GitHub Pages

1. Go to **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: `main` · Folder: `/ (root)`
4. Save — your URL will be `https://yourusername.github.io/people-briefing/`

### 3. Get your Anthropic API key

1. Go to [console.anthropic.com](https://console.anthropic.com) → API Keys
2. Create a new key and copy it

### 4. Get your Google OAuth credentials (the one-time part)

You need three values: a **Client ID**, **Client Secret**, and a **Refresh Token** for `omary@islamicfamily.ca`.

**Step A — Create OAuth credentials**
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project (or use an existing one)
3. Enable the **Google Calendar API** and **Gmail API**
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
5. Application type: **Desktop app**
6. Download the JSON — you'll need `client_id` and `client_secret`

**Step B — Get a refresh token**

Run this locally (one time only):

```bash
pip install google-auth-oauthlib

python3 - <<'EOF'
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
creds = flow.run_local_server(port=0)

print("CLIENT_ID:     ", creds.client_id)
print("CLIENT_SECRET: ", creds.client_secret)
print("REFRESH_TOKEN: ", creds.refresh_token)
EOF
```

A browser window will open — sign in as `omary@islamicfamily.ca` and grant access.
Copy the three values printed at the end.

### 5. Add secrets to GitHub

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

Add these four secrets:

| Secret name            | Value                        |
|------------------------|------------------------------|
| `ANTHROPIC_API_KEY`    | Your Anthropic API key       |
| `GOOGLE_CLIENT_ID`     | From Step 4                  |
| `GOOGLE_CLIENT_SECRET` | From Step 4                  |
| `GOOGLE_REFRESH_TOKEN` | From Step 4                  |

### 6. Test it

Go to **Actions → Generate People & Purpose Briefing → Run workflow**

Select `workflow_dispatch`, optionally enter a target date (YYYY-MM-DD), and click **Run workflow**.

After ~60 seconds, check that `index.html` was committed and your GitHub Pages URL shows the updated briefing.

---

## Schedule

The workflow runs automatically at **5:00 PM Mountain Time every day**, generating the briefing for the following day (today + tomorrow).

To change the time, edit the cron line in `.github/workflows/generate-briefing.yml`:

```yaml
- cron: '0 23 * * *'   # 23:00 UTC = 5pm MDT (UTC-6)
                        # Change to '0 22 * * *' for 4pm, etc.
```

Note: During Mountain Standard Time (winter, UTC-7), 5pm MST = 00:00 UTC (midnight).
You may want two cron entries to handle both:

```yaml
- cron: '0 23 * * *'   # 5pm MDT (summer)
- cron: '0 0 * * *'    # 5pm MST (winter)
```

---

## Sharing with your team

Share the GitHub Pages URL directly. No login required — the briefing is a static HTML file.
The page regenerates automatically each evening so the link is always current.
