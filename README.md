# 🕸️ Dragnet

Family torrent search over a self-hosted DHT index, with one-click **Send to put.io**.
Downloads land in put.io, where an existing rclone cron pulls them into Plex.

## How it works

```
family browsers ──► Cloudflare Access ──► tunnel ──► cloudflared ─┐
  (Google / email OTP, policy in Zero Trust)                      │
                                                                  ▼
LAN browsers, Claude Code ─────────────────► dragnet web (Django, :9180)
                                    │ search/filter/sort, download log (SQLite)
                                    ▼ GraphQL (internal network)
                                bitmagnet (DHT crawler + indexer)
                                    ▼
                                postgres (the torrent metadata index)

dragnet web ──► api.put.io /v2/transfers/add
```

- [bitmagnet](https://bitmagnet.io) crawls the BitTorrent DHT continuously and indexes
  torrent metadata into Postgres, classifying content (movies/TV/music/…) along the way.
  Its own UI/API has no auth, so it is **not** published on the host — only the Django app is.
- The Django app (`core/`) provides the search UI (filter by type / resolution /
  year, sort by seeders / size / date) and sends magnets to put.io, recording who
  sent what.
- Public access goes through a Cloudflare tunnel, with Cloudflare Access in front
  doing the authentication. See [Public access](#public-access-via-cloudflare).
- Adult content lives in a separate, permission-gated section. See
  [Adult content](#adult-content).

## Public access via Cloudflare

`https://dragnet.jensenbox.com` is a Cloudflare tunnel to the `web` container.
Cloudflare Access authenticates every visitor before the request reaches the
tunnel, and forwards a signed JWT that `core/cfaccess.py` validates.

Why the JWT and not the much simpler `Cf-Access-Authenticated-User-Email`
header: port 9180 is still open on the LAN and bypasses Cloudflare completely,
so anyone on the network could set that header and become any user. Only the
RS256 signature — checked against the team's published keys and pinned to this
application's audience tag — actually proves anything.

Family members need no password: Access verifies their identity, and Django
provisions an account for the email on first visit (matching an existing account
by email if there is one, so accounts created in `/admin/` keep their username
and privileges). Django's own login page still works on the LAN for the
superuser.

**First-time setup** (Cloudflare dashboard, then the server):

1. Zero Trust → Networks → Tunnels → **Create a tunnel** (Cloudflared), name it
   `dragnet`. Copy the **tunnel token** from the install command.
2. On that tunnel, add a **public hostname**: `dragnet.jensenbox.com` →
   type `HTTP` → URL `web:8000`. That is the compose *service name*, not the
   host IP — cloudflared shares the stack's network. This creates a proxied
   CNAME that takes precedence over the existing `*.jensenbox.com` wildcard A
   record.
3. Zero Trust → Access → Applications → **Add a self-hosted application** for
   `dragnet.jensenbox.com`. Copy its **Application Audience (AUD) Tag**.
4. Add a policy: action *Allow*, include → **Emails** → the family addresses.
5. In `/opt/stacks/dragnet/.env` set `CF_TUNNEL_TOKEN`, `CF_ACCESS_TEAM_DOMAIN`,
   `CF_ACCESS_AUD`, `COMPOSE_PROFILES=tunnel`, `CSRF_TRUSTED_ORIGINS`,
   `SECURE_COOKIES=true`, and add the hostname to `ALLOWED_HOSTS`.
6. `docker compose up -d`.

Verify with `dig +short dragnet.jensenbox.com` — it should return Cloudflare
edge IPs (`104.x` / `172.67.x`) rather than the home WAN address.

The `cloudflared` service sits behind a compose profile, so the stack still
comes up normally on a host with no tunnel token.

### What stays LAN-only

`/api/download/` is not covered by Access — Claude Code reaches it directly on
`192.168.16.10:9180`. Requesting it through the public hostname would hit the
Access login redirect instead; add an Access **Bypass** policy for `/api/*` if
that is ever wanted. bitmagnet's dashboard on `:3333` has no auth at all and
must never be published.

## Adult content

Adult content is a separate section at `/adult/`, not a filter on the main
search:

- **Indexed, not discarded.** `CLASSIFIER_DELETE_XXX=false` keeps `xxx` in the
  index. This is a one-way door in the other direction — while it is `true`,
  bitmagnet writes every adult infohash into its `blocked_torrents` bloom filter
  and never re-crawls it, so the history lost while it was on is not
  recoverable. Do not try to recover it by clearing that filter: the same filter
  holds torrents deleted by the classifier's banned-keywords rule.
- **Gated by a permission**, `core.view_adult_content`. Grant it per user or via
  a group in `/admin/`. Without it the nav link is absent and both `/adult/`
  URLs return 403.
- **Excluded from family search at the query level.** bitmagnet has no "exclude"
  facet, so non-adult search sends an explicit allow-list of every other content
  type *plus `null`* — unclassified is ~4.5M of the 8.3M-row index, and omitting
  it would hide most of the index. A crafted `?content_type=xxx` falls back to
  the allow-list rather than passing through.
- **Routed away from Plex.** Adult sends go to a root-level `adult/` folder on
  put.io, outside the rclone-watched `plex/` folder, so they are never shipped
  to the media server.

The permission is enforced in `core/services.py`, below both the view and the
JSON API, so the two cannot diverge. The API returns `403` for `xxx` unless the
API user has been granted the permission.

Caveat worth knowing: the split is only as good as bitmagnet's classifier.
Adult torrents it fails to classify stay in the `null` bucket and remain
reachable from family search.

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
bootstrapped from `DJANGO_SUPERUSER_*` in `.env`). Family members who log in
through Cloudflare Access are provisioned automatically — `/admin/` is where you
disable one, or grant the adult permission.

### Notes

- The DHT index starts empty; popular content appears within hours, the long tail
  builds over days/weeks. Postgres grows to tens of GB over months.
- bitmagnet dashboard (crawler throughput, queue backlog, torrent metrics):
  http://192.168.16.10:3333 — no auth, LAN-trusted only; never port-forward it.
  Linked from `/status/` for staff via `BITMAGNET_DASHBOARD_URL`.
- Secrets live only in `/opt/stacks/dragnet/.env` (see `.env.example`). Unlike
  the other stacks, dragnet's `.env` is **not** in the SOPS map in
  `/opt/stacks/secrets.sh`, so none of it is backed up.
