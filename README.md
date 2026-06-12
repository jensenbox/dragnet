# 🕸️ Dragnet

Family torrent search over a self-hosted DHT index, with one-click **Send to put.io**.
Downloads land in put.io, where an existing rclone cron pulls them into Plex.

## How it works

```
family browsers ──► dragnet web (Django, :9180)   ← the only LAN-exposed service
                        │ login, search/filter/sort, download log (SQLite)
                        ▼ GraphQL (internal network)
                    bitmagnet (DHT crawler + indexer)
                        ▼
                    postgres (the torrent metadata index)

dragnet web ──► api.put.io /v2/transfers/add
```

- [bitmagnet](https://bitmagnet.io) crawls the BitTorrent DHT continuously and indexes
  torrent metadata into Postgres, classifying content (movies/TV/music/…) along the way.
  Its own UI/API has no auth, so it is **not** published on the host — only the Django app is.
- The Django app (`core/`) provides session auth, the search UI (filter by type /
  resolution / year, sort by seeders / size / date), and sends magnets to put.io,
  recording who sent what.
- Adult content is dropped at classification time (`CLASSIFIER_DELETE_XXX=true`).

## Development

```bash
uv sync                  # install deps
uv run pytest            # tests
uv run ruff check .      # lint
uv run ruff format .     # format

# Full stack locally (bitmagnet + postgres + web with runserver/bind mount):
docker compose -f docker-compose.yml -f compose.dev.yml up --build
```

## Deployment (192.168.16.10)

`/opt/stacks/dragnet/` is a **git checkout of this repo** — `docker-compose.yml` comes
from git, while `.env`, `data/` and `config/` are untracked local state. The nightly
`update-everything.sh` does `git pull --ff-only` on git-managed stack dirs before
`docker compose pull`, so both compose changes and new images deploy automatically.

Fresh setup:

```bash
git clone https://github.com/jensenbox/dragnet.git /opt/stacks/dragnet
cd /opt/stacks/dragnet
# create .env from .env.example
mkdir -p data/web && chown 1000:1000 data/web   # SQLite volume, written by uid 1000
docker compose up -d
```

The image is built and pushed to `ghcr.io/jensenbox/dragnet:latest` by GitHub Actions
on every push to `main`.

User accounts are managed in Django admin at `/admin/` (the first superuser is
bootstrapped from `DJANGO_SUPERUSER_*` in `.env`).

### Notes

- The DHT index starts empty; popular content appears within hours, the long tail
  builds over days/weeks. Postgres grows to tens of GB over months.
- bitmagnet dashboard (crawler throughput, queue backlog, torrent metrics):
  http://192.168.16.10:3333 — no auth, LAN-trusted only; never port-forward it.
- Secrets live only in `/opt/stacks/dragnet/.env` (see `.env.example`).
