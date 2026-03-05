# ⬡ CyberCIA Lookup

> **OSINT username intelligence across 3,000+ platforms — all checks run directly in your browser.**

![CyberCIA Lookup UI](maigret/web/static/cybercia.png)

---

## Features

- **Browser-side search engine** — when a visitor hits the site, JavaScript runs all platform checks in their browser via concurrent `fetch()` calls. Zero Python processing per search.
- **3,000+ platforms** — social networks, coding sites, forums, gaming, photo sharing, and more
- **Real-time progress bar** — live counter and streaming result cards as profiles are found
- **Export results** — download found profiles as JSON or CSV
- **REST API** — full programmatic access with async job support
- **Dark cyber UI** — Orbitron font, cyan glow theme, CyberCIA branding

---

## Quick Start

### Requirements
- Python 3.10+
- `poetry` or `pipx`

### Run with pipx
```bash
pipx install .
cybercia --web 5000
```

### Run with poetry
```bash
poetry install
poetry run python -m maigret --web 5000
```

Then open **http://127.0.0.1:5000**

---

## REST API

Base URL: `http://127.0.0.1:5000/api/v1`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Service status + version |
| `GET` | `/db?limit=N` | Sites database with URL patterns (for client-side JS) |
| `GET` | `/proxy?url=<url>` | HTTP proxy for CORS bypass |
| `GET` | `/sites?limit=N&tag=X` | Browse available sites |
| `POST` | `/search` | Start async username lookup job |
| `GET` | `/status/<job_id>` | Poll job status |
| `GET` | `/progress/<job_id>` | Real-time progress (checked/total/found) |
| `GET` | `/results/<job_id>` | Full JSON results |
| `DELETE` | `/jobs/<job_id>` | Clear job from memory |

### Example: Start a search via API
```bash
curl -X POST http://127.0.0.1:5000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"usernames": ["johndoe"], "options": {"top_sites": 200, "timeout": 15}}'
```

```json
{
  "job_id": "20240101_120000_123456",
  "status": "running",
  "status_url": "/api/v1/status/...",
  "results_url": "/api/v1/results/..."
}
```

All `/api/*` routes include **CORS headers** — accessible from any origin.

---

## How It Works

```
Visitor opens site
       │
       ▼
Browser fetches /api/v1/db  ──→  Gets list of 3000+ sites + URL patterns
       │
       ▼
Browser JS runs 20 parallel checks at a time
  └─→  fetch(/api/v1/proxy?url=https://github.com/username)
  └─→  Server proxies the request (CORS bypass)
  └─→  JS checks status code / response body
       │
       ▼
Results stream into the page in real time
```

The server's only job is **serving files + acting as a thin HTTP proxy**. All OSINT logic runs in the visitor's browser.

---

## Project Structure

```
maigret/
├── web/
│   ├── app.py              # Flask app — API endpoints + proxy
│   ├── templates/
│   │   ├── base.html       # Base layout (dark cyber theme)
│   │   └── index.html      # SPA — client-side search engine
│   └── static/
│       └── cybercia.png    # Logo
├── resources/
│   └── data.json           # Sites database (3000+ platforms)
├── checking.py             # Async HTTP checking engine
├── sites.py                # Site/database models
└── ...
```

---

## License

MIT — see [LICENSE](LICENSE)
