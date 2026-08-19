# AI Education News (AI-Edu-RSS)

AI-Edu-RSS is a free, lightweight AI + Education news aggregator built for the default MagicMirror² `newsfeed` module on a Raspberry Pi. A Python script reads trusted RSS/Atom sources, requires both AI and education relevance, removes duplicates, favours recent and authoritative stories, and writes a standard RSS 2.0 feed.

- Python aggregation with no paid API or database
- GitHub Actions update every hour
- GitHub Pages static hosting
- Mobile-friendly browser page
- MagicMirror²-compatible RSS 2.0 output

## Public URLs

- News page: <https://jlcloudtea.github.io/ai-education-news/>
- RSS feed: <https://jlcloudtea.github.io/ai-education-news/aggregated_feed.xml>

The first Pages deployment may take a few minutes after the repository is created.

## How it works

1. Reads enabled RSS/Atom sources from `config/feeds.json`.
2. Matches at least one AI keyword **and** at least one education keyword from `config/keywords.json`.
3. Rejects likely finance, GPU/chip, fundraising, and general product stories unless they contain a strong education signal.
4. Deduplicates canonical URLs, exact titles, and highly similar titles.
5. Prioritises stories from the last 72 hours, then backfills from the last 14 days if needed.
6. Publishes up to 30 items, newest first, to `docs/aggregated_feed.xml`.

## Add, remove, or disable a news source

Edit `config/feeds.json`. Each source has a display name, RSS/Atom URL, authority priority, and enabled flag:

```json
{
  "name": "Example Education News",
  "url": "https://example.org/feed.xml",
  "priority": 8,
  "enabled": true
}
```

- Add an object to add a source.
- Set `"enabled": false` to temporarily disable it.
- Delete the object to remove it.
- Use a higher `priority` for original or authoritative publishers.

One failed feed is logged and skipped; it does not stop the run.

## Change keywords or output limits

Edit `config/keywords.json`:

- `ai_keywords`: AI terms that qualify a story.
- `education_keywords`: education terms that qualify a story.
- `strong_education_keywords`: clear education signals used to reject finance/hardware noise.
- `non_education_context`: common non-education contexts.
- `hard_exclude_context`: phrases that are never useful for this education-news display.
- `settings`: age windows, maximum item count, timeout, and title-similarity threshold.

Matching is case-insensitive. The aggregator removes phrases such as “machine learning” before testing the standalone education keyword “learning”, preventing ordinary ML articles from being treated as education news.

## Run and test locally

Python 3.11 or newer is recommended.

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python rss_aggregator.py
python -m unittest discover -s tests -v
python -c "import xml.etree.ElementTree as ET; ET.parse('docs/aggregated_feed.xml'); print('RSS XML is valid')"
```

Open `docs/index.html` through a local HTTP server (rather than `file://`) to test the page:

```bash
python -m http.server 8000 --directory docs
```

Then visit <http://localhost:8000/>.

## GitHub Actions and Pages

`.github/workflows/update-rss.yml` runs at minute 17 of every hour and supports manual runs from the Actions tab. It installs the minimal dependencies, generates and validates the RSS, commits `docs/aggregated_feed.xml` only when it changed, and deploys `docs/` to GitHub Pages using the repository-provided `GITHUB_TOKEN`.

The workflow requests only the permissions needed to update repository content and deploy Pages. No token, password, or third-party API key is stored in the repository.

## MagicMirror² configuration

Add this module entry to `~/MagicMirror/config/config.js`:

```js
{
    module: "newsfeed",
    position: "bottom_bar",
    config: {
        feeds: [
            {
                title: "AI Education News",
                url: "https://jlcloudtea.github.io/ai-education-news/aggregated_feed.xml",
                encoding: "UTF-8"
            }
        ],
        showSourceTitle: true,
        showPublishDate: true,
        showDescription: true,
        lengthDescription: 400,
        reloadInterval: 5 * 60 * 1000
    }
}
```

These options are supported by the current default MagicMirror² `newsfeed` module. Restart MagicMirror after editing the configuration.

Official references: [MagicMirror² newsfeed configuration](https://docs.magicmirror.builders/modules/newsfeed.html) and [GitHub Pages custom workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Future extensions

The configuration and pipeline are intentionally separate so later versions can add Chinese translation, one-sentence summaries, LLM relevance scores, Top 10 selection, Australia/global and K–12/higher-education categories, images, RSSHub sources, or Telegram/email delivery without replacing the V1 feed format.
