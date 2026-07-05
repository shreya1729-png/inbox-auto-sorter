# Inbox Auto-Sorter — Web Version

A hosted version: visitor clicks "Connect Gmail", logs in, we sort their inbox
and draft replies, then show a results page. No screen-sharing needed per client.

## What's different from the desktop script
- Runs as a real website (Flask), not a local script
- Uses a **Web application** OAuth client (not Desktop)
- Deployed to Render so it has a real public URL

## Step 1 — Push this folder to GitHub
Render deploys from a GitHub repo, so:
1. Create a new repo on GitHub (e.g. `inbox-auto-sorter`)
2. Upload these files into it: `app.py`, `requirements.txt`, `Procfile`

## Step 2 — Create a Render account and new Web Service
1. Go to https://render.com and sign up (free)
2. Click **New +** → **Web Service**
3. Connect your GitHub repo
4. Runtime: Python 3
5. Build command: `pip install -r requirements.txt`
6. Start command: `gunicorn app:app`
7. Click **Create Web Service** — Render will assign you a public URL like
   `https://inbox-auto-sorter.onrender.com` (don't worry if the first deploy fails —
   we still need to add environment variables below, then redeploy)

## Step 3 — Create a NEW Google OAuth client (Web application type)
Your existing `credentials.json` was a **Desktop app** client — that won't work here.
You need a second, separate OAuth client of type **Web application**:

1. Go to https://console.cloud.google.com → your project → **APIs & Services** → **Credentials**
2. **Create Credentials** → **OAuth client ID**
3. Application type: **Web application**
4. Under **Authorized redirect URIs**, click **Add URI** and enter:
   ```
   https://YOUR-RENDER-URL.onrender.com/oauth2callback
   ```
   (use the actual URL Render gave you in Step 2)
5. Click **Create**
6. Click **Download JSON** — this file's *entire contents* get pasted into an
   environment variable in the next step (don't rename it to credentials.json,
   you won't need it as a file at all)

## Step 4 — Add environment variables on Render
In your Render service → **Environment** tab, add these:

| Key | Value |
|---|---|
| `OPENAI_API_KEY` | your OpenAI key |
| `FLASK_SECRET_KEY` | any random string, e.g. `a8f3k29d...` (mash your keyboard) |
| `GOOGLE_CLIENT_CONFIG` | the **entire contents** of the Web application JSON you just downloaded, pasted as one line |

Save, then **Manual Deploy** → **Deploy latest commit** to restart with the new variables.

## Step 5 — Add test users
Same as before: Google Cloud Console → **OAuth consent screen** → **Test users** →
add each business's Gmail address (up to 100 allowed while unverified).

## Step 6 — Try it
Visit your Render URL in a browser. Click **Connect Gmail**, log in with a test user
account, click through the "unverified app" warning, and you should land on a page
showing which emails got sorted into which category.

## Sending this to your 5 pilot businesses
Once it works for you:
1. Add each of their Gmail addresses as test users (Step 5)
2. Send them the Render link directly — they click it, log in themselves, done.
   No screen-share needed.

## Known limitations to be upfront about
- Free Render tier "spins down" after inactivity — the first visit after a while
  can take 15-30 seconds to wake up. Fine for a pilot, worth upgrading later.
- Each visit re-processes the same batch of recent emails (no memory of what
  was already processed) — fine for a demo/pilot, would need a database to track
  "already processed" emails for a real repeat-use product.
- Still limited to your 100 test users until you go through Google's verification
  process — completely fine for pilots with a handful of businesses.
