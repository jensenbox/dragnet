# CLAUDE.md — Dragnet

Family torrent search over a self-hosted bitmagnet DHT index, with sends to put.io.
Deployed at `/opt/stacks/dragnet/` on 192.168.16.10 (web UI: `:9180`, bitmagnet:
`:3333`), and published to family members at https://dragnet.jensenbox.com via a
Cloudflare tunnel with Cloudflare Access in front.

Use the LAN addresses below, not the public hostname: Access would answer a
programmatic request with a login redirect, not JSON.

## Handling "download X" requests (e.g. "all seasons of Westworld in 4K")

You have a full programmatic path. The division of labor: **bitmagnet GraphQL for
search, your judgment for picking, the dragnet API for sending.** Never call put.io
directly — the dragnet API is the single code path that owns folder routing
(movie → `plex/curated_movies`, tv_show → `plex/tv_series`, unclassified →
root `unclassified/`), duplicate detection, and family-visible history.

### 1. Search the index — bitmagnet GraphQL

`POST http://192.168.16.10:3333/graphql` (no auth, LAN only):

```graphql
query ($input: TorrentContentSearchQueryInput!) {
  torrentContent { search(input: $input) {
    totalCount
    items {
      infoHash title contentType videoResolution videoSource videoCodec
      seeders leechers publishedAt
      episodes { label seasons { season episodes } }
      content { title releaseYear }
      torrent { name size filesCount magnetUri }
    }
  } }
}
```

Variables, e.g.: `{"input": {"queryString": "westworld", "limit": 50,
"orderBy": [{"field": "seeders", "descending": true}],
"facets": {"contentType": {"filter": ["tv_show"]},
"videoResolution": {"filter": ["V2160p"]}}}`

**Lean on the pre-classified facets, not query-string keywords.** Keep
`queryString` to the bare title and express resolution ("4K" → `V2160p`),
content type, source, language, and year as facet filters — bitmagnet parsed
release names into structured fields precisely so you don't string-match
"2160p|4K|UHD" yourself. One caveat: torrents the classifier couldn't assign a
resolution have `videoResolution: null` and a strict facet filter hides them —
if results look thin, re-run without the resolution facet and judge those rows
by name/size.

### 2. Pick — judgment, not just filters

- Prefer one complete-series pack over per-season packs over loose episodes;
  use `episodes.seasons` to verify coverage, and fill gaps with season packs.
- Sanity-check size vs resolution (a 2160p season under ~10 GB is junk/fake).
- Prefer seeders > ~5; check `videoSource` (BluRay/WEBDL > WEBRip ≫ CAM).
- Cross-check `content.releaseYear`/title to avoid same-name remakes.

### 3. Send — dragnet API

`POST http://192.168.16.10:9180/api/download/` with
`Authorization: Bearer $DRAGNET_API_TOKEN` (token: `grep DRAGNET_API_TOKEN
/opt/stacks/dragnet/.env` over SSH). JSON body:

```json
{"info_hash": "…", "title": "…", "magnet_uri": "…",
 "content_type": "tv_show", "size": 123}
```

`content_type` must be the bitmagnet `contentType` verbatim — it drives folder
routing. Responses: `201` sent (includes `destination`), `409` duplicate (someone
already sent it — report this, only re-send with `"force": true` if the user asks;
**family content only** — adult sends are never duplicates), `403` adult content
without permission, `502` put.io failure, `400` bad payload. API sends show up in
History as user `claude` — except adult ones, which are not recorded at all.

### Adult content

`contentType: xxx` is a separate section in the web UI and a separate put.io
folder (`adult/`, outside the rclone-watched `plex/`). Two consequences for you:

- Searching bitmagnet directly will return `xxx` results, because GraphQL has no
  permission model. Do not send them unless the user asked for adult content.
- The dragnet API returns `403` for `xxx` unless the `claude` user has been
  granted `core.view_adult_content` in `/admin/`. That is deliberate: report the
  403 rather than working around it.
- **Adult sends are never recorded.** No row on success, no row on failure, not
  even the title. So there is no duplicate detection for `xxx` (the same torrent
  will send twice, making two put.io transfers, and you will never get a `409`),
  and nothing to check against beforehand — ask the user rather than assuming a
  re-send is safe.

### 4. Report

Tell the user what was sent, to which folder, and anything skipped (duplicates,
gaps in coverage, suspicious torrents). Downloads flow: put.io → hourly rclone →
`/mnt/data/putio` → Plex.

## Development

```bash
uv sync && uv run pytest        # tests
uv run ruff check . && uv run ruff format .
```

Deploy: push to `main` → CI builds `ghcr.io/jensenbox/dragnet:latest` →
`update-everything.sh` on the server auto-pulls nightly. For an immediate deploy:
`ssh 192.168.16.10 'cd /opt/stacks/dragnet && git pull --ff-only && docker compose pull && docker compose up -d'`
(the `git pull` matters: the compose file itself is versioned, so without it the
new image runs under the old compose and never sees new environment variables)
(wait for CI to finish first).

Secrets live only in `/opt/stacks/dragnet/.env`. SQLite (download history) is the
only state worth backing up; the bitmagnet Postgres index is a rebuildable cache
and is excluded from the nightly backup.

Two traps when changing the deployment:

- `CLASSIFIER_DELETE_XXX=true` does not merely hide adult content; bitmagnet
  blocklists every adult infohash it sees in the `blocked_torrents` bloom filter
  and never re-crawls it. Turning it back on loses index history permanently.
- The `cloudflared` service is behind the `tunnel` compose profile, enabled by
  `COMPOSE_PROFILES=tunnel` in `.env`. This keeps the nightly
  `update-everything.sh` (`git pull` + plain `docker compose up -d`) working on a
  host with no tunnel token.
