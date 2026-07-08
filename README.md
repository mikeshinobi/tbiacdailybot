# Daily WordPress Content Bot

Generates one blog post (with a header image) per day using Claude + an image
API, and creates it as a draft in your WordPress site.

## What you need before starting

1. A **Claude API key** — from https://console.anthropic.com (Settings → API Keys)
2. An **OpenAI API key** — from https://platform.openai.com (used only for image
   generation; you'll need billing enabled there)
3. A **WordPress Application Password** — in your WP admin:
   Users → Profile → scroll to "Application Passwords" → enter a name like
   "daily-bot" → Add New Application Password → copy the generated password
   (it looks like `abcd 1234 efgh 5678`)

## One-time setup (about 10 minutes)

1. **Create a GitHub repository** (if you don't have one) and put these files
   in it: `generate_daily_post.py`, `.github/workflows/daily-post.yml`, and
   this README.

2. **Add your secrets.** In your GitHub repo: Settings → Secrets and variables
   → Actions → New repository secret. Add each of these one at a time:
   - `ANTHROPIC_API_KEY`
   - `OPENAI_API_KEY`
   - `WP_BASE_URL` — your site's URL, e.g. `https://yourblog.com` (no trailing slash)
   - `WP_USERNAME` — your WordPress username
   - `WP_APP_PASSWORD` — the application password from step 3 above

3. **Edit the workflow file** (`.github/workflows/daily-post.yml`) to set:
   - `SITE_TOPIC` — a short description of what your site covers
   - `SITE_VOICE` — the tone you want (e.g. "witty and conversational" or
     "professional and concise")
   - The cron schedule, if 13:00 UTC doesn't suit you (use https://crontab.guru
     to build a different schedule)

4. **Test it manually first.** In your GitHub repo: Actions tab → "Daily
   WordPress Post" → "Run workflow" button. Watch the run's logs. If it
   succeeds, check your WordPress dashboard — you should see a new **draft**
   post waiting for you.

5. Once you're happy with a few days of output, you can either keep it as
   drafts you approve each morning (recommended), or change `POST_STATUS` to
   `publish` in the workflow file to make it fully automatic.

## Running it locally to test (optional, before pushing to GitHub)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
export WP_BASE_URL=https://yourblog.com
export WP_USERNAME=your-username
export WP_APP_PASSWORD="abcd 1234 efgh 5678"
export SITE_TOPIC="a blog about home coffee brewing"
export SITE_VOICE="warm, knowledgeable, a little nerdy about gear"

python3 generate_daily_post.py
```

## Cost

Roughly $0.05–$0.20 per post (mostly the image), so well under $5/month
running daily. See the script's `IMAGE_QUALITY` env var ("low", "medium",
"high") if you want to trade image quality for cost.

## Troubleshooting

- **401/403 from WordPress**: double check the Application Password was
  copied exactly (spaces included), and that your host isn't blocking the
  REST API (`/wp-json/wp/v2/posts` should return JSON if you visit it in a
  browser).
- **Claude returns invalid JSON**: this is rare but can happen; check the
  Action logs for the raw output. Re-running usually fixes it.
- **Image generation fails**: make sure billing is enabled on your OpenAI
  account — the image API has no meaningful free tier.
