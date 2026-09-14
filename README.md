# micronote.pub

<p align="center">A self-hosted, single-user, <a href="https://activitypub.rocks">ActivityPub</a> powered microblog.</p>

Forked from <a href="https://github.com/tsileo/microblog.pub">microblog.pub</a>, modernized: SQLite storage via <a href="https://github.com/cwt/neosqlite">NeoSQLite</a>, federation via <a href="https://github.com/cwt/active-boxes">active-boxes</a>, no task broker, plain CSS.

## Features

 - Implements a basic [ActivityPub](https://activitypub.rocks/) server (with federation)
   - Compatible with [Mastodon](https://github.com/tootsuite/mastodon) and others (Pleroma, Hubzilla...)
   - Also implements a remote follow compatible with Mastodon instances
 - Exposes your outbox as a basic microblog
 - Implements [IndieAuth](https://indieauth.spec.indieweb.org/) endpoints (authorization and token endpoint)
   - WebAuthn security-key second factor for the admin login
   - You can use your ActivityPub identity to login to other websites/app
 - Comes with an admin UI with notifications and the stream of people you follow
 - Allows you to attach files to your notes
   - Privacy-aware image upload endpoint that strip EXIF meta data before storing the file
 - No JavaScript needed to read or post — pages work with scripting disabled
   (only the security-key ceremonies use a small inline script).
   Even the admin UI is plain HTML/CSS otherwise
 - Easy to customize (plain CSS with light/dark custom properties)
   - mobile-friendly theme
   - with dark and light version
   - installable web app manifest (icons, theme color)
 - Microformats aware (exports `h-feed`, `h-entry`, `h-cards`, ...)
 - Exports RSS/Atom/[JSON](https://jsonfeed.org/) feeds
    - You stream/timeline is also available in an (authenticated) JSON feed
 - Comes with a tiny HTTP API to help posting new content and and read your inbox/notifications
 - Easy to "cache" (the external/public-facing microblog part)
   - With a good setup, cached content can be served most of the time
   - You can setup a "purge" hook to let you invalidate cache when the microblog was updated
 - Deployable with Docker (Docker compose for everything: dev, test and deployment)
 - Single SQLite file storage (no database server), background jobs via a built-in worker (no broker)
 - Focused on testing
   - The core ActivityPub code/tests are in [active-boxes](https://github.com/cwt/active-boxes)
   - Local ActivityPub matrix harness spec (see `docs/migration.md`) for covering all supported actions without the fediverse
   - CI runs lint, integration tests and a Docker build; "federation" tests run manually against two instances (`make reload-fed`)

## ActivityPub

micronote.pub implements an [ActivityPub](http://activitypub.rocks/) server, it implements both the client to server API and the federated server to server API.

Activities are verified using HTTP Signatures or by fetching the content on the remote server directly.

## Running your instance

### Installation

Requires Python 3.11.

```shell
$ pip install -r requirements.txt
$ cp config/me.sample.yml config/me.yml
``` 

### Configuration

```shell
$ make password
Password: <enter a password; nothing will show on screen>
$2b$12$iW497g...
```

Edit `config/me.yml` to add the above-generated password, like so:

```
username: 'username'
name: 'Your Name'
icon_url: 'https://you-avatar-url'
domain: 'your-domain.tld'
summary: 'your summary'
https: true
pass: $2b$12$iW497g...
```

### Deployment with Docker or Podman

Build the image (tagged `micronote:latest`):

```shell
$ make docker
# ...or directly:
$ docker build -t micronote:latest .
```

Point the `.env` file at your directories (defaults work for a first run):

```
WEB_PORT=5005
CONFIG_DIR=./config
DATA_DIR=./data
```

`CONFIG_DIR` must contain your `me.yml` (see Configuration above); the
secret keys are generated on first boot. Then start both services —
the web app and the background worker share one SQLite file, so they
must mount the **same** `DATA_DIR`:

```shell
$ docker compose up -d
$ docker compose ps
$ docker compose logs -f web worker
```

Podman works the same way — just swap the binary:

```shell
$ podman build -t micronote:latest .
$ podman compose up -d
```

Notes for Podman / rootless / RHEL hosts:

- Rootless Podman can bind port 5005 without extra privileges (ports
  above 1024 need no special setup).
- On SELinux systems (RHEL 8/9) the bind mounts need relabeling or the
  containers won't be able to read `config/` and `data/` — either use
  `:Z` suffixes on the volumes (e.g. `"${CONFIG_DIR}:/app/config:Z"`) or
  run `chcon -Rt container_file_t config data` once.
- `podman-compose` (the separate Python project) also accepts these
  files; `podman compose` (built into Podman 4.1+) is preferred.

### Manual run behind nginx (SSL termination on nginx)

The app itself only speaks plain HTTP; TLS ends at nginx. Absolute
URLs are built from `domain` / `https: true` in `me.yml`, so no
proxy-fix configuration is needed — just forward the original `Host`.

1. Configure `config/me.yml` with your public domain and `https: true`,
   then start both processes (indexes are created by `run.sh`):

```shell
$ ./run.sh    # gunicorn on 0.0.0.0:5005
$ ./worker.sh # background federation worker (run exactly one)
```

For a persistent install, wrap each in systemd. Minimal example
(`/etc/systemd/system/micronote-web.service`, and likewise
`micronote-worker.service` with `ExecStart=.../worker.sh`):

```ini
[Unit]
Description=micronote web
After=network.target

[Service]
User=micronote
WorkingDirectory=/srv/micronote.pub
ExecStart=/srv/micronote.pub/run.sh
Restart=always

[Install]
WantedBy=multi-user.target
```

2. Proxy through nginx with certbot-managed certificates:

```shell
$ sudo dnf install nginx python3-certbot-nginx   # or apt install ...
$ sudo certbot --nginx -d your-domain.tld
```

Then make sure the nginx server block looks like this (certbot
writes the `ssl_certificate` lines for you):

```nginx
server {
    listen 443 ssl;
    server_name your-domain.tld;

    ssl_certificate     /etc/letsencrypt/live/your-domain.tld/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.tld/privkey.pem;

    # uploads are capped at 10 MB by the app
    client_max_body_size 12m;

    location / {
        proxy_pass http://127.0.0.1:5005;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name your-domain.tld;
    return 301 https://$host$request_uri;
}
```

`Host` must be forwarded unchanged — remote servers address your
actor/inbox by that name, and HTTP Signatures cover it.

## Development

The most convenient way to hack on micronote.pub is to run the server locally, and the worker alongside it:


```shell
# One-time setup
$ pip install -r requirements.txt
# Run the background worker (the dev compose only starts the worker)
$ docker-compose -f docker-compose-dev.yml up -d
# Run the server locally
$ FLASK_DEBUG=1 MICRONOTE_DEBUG=1 FLASK_APP=app.py flask run -p 5005 --with-threads
# ...or skip the worker and run jobs inline instead:
$ MICRONOTE_TASK_EAGER=1 FLASK_DEBUG=1 MICRONOTE_DEBUG=1 FLASK_APP=app.py flask run -p 5005 --with-threads
```

Local runs need indexes once (Docker does this via `run.sh`):

```shell
$ python -c "import config; config.create_indexes()"
```

Use the fixture identity for local work (no need to invent one):

```shell
$ cp tests/fixtures/me.yml config/me.yml
```

### Simulating a remote node locally

`scripts/stub_remote.py` is a fake second ActivityPub server and
`scripts/ap_matrix.py` drives its fixtures through your instance —
no real fediverse involved. Your instance **must** run with
`MICRONOTE_DEBUG=1` so it accepts plain-HTTP fetches from the stub,
and eager mode keeps everything deterministic:

```shell
# terminal 1: your instance (eager = no worker needed)
$ MICRONOTE_TASK_EAGER=1 FLASK_DEBUG=1 MICRONOTE_DEBUG=1 \
  FLASK_APP=app.py flask run -p 5005 --with-threads

# terminal 2: the fake remote node
$ python scripts/stub_remote.py --port 5006

# terminal 3: run the matrix (follow, note, like, unsigned fallback)
$ python scripts/ap_matrix.py all
```

What gets exercised: a signed remote Follow (expect an auto-Accept
delivered back, with its HTTP Signature strictly verified by the
stub), a signed remote note (expect the stream flag in the DB and no
outbound delivery), a signed Like on a local note (expect the
counter), and an unsigned Follow (expect acceptance via the
fetch-fallback path). Single cases run as
`python scripts/ap_matrix.py follow` (also `create`, `like`,
`unsigned-follow`); `--peer`/`--stub` override the default
`localhost:5005`/`localhost:5006`. The full harness spec lives in
`docs/migration.md` (Phase 7).

### Cleaning up duplicate follows

A follower relationship is keyed by actor, but older versions stored
one row per Follow *activity*, so repeats (same actor, new activity
id) could pile up. Current code drops those at ingest, and
`dedup.py` cleans rows that predate the fix — inbox follows keyed by
actor, outbox follows keyed by object, first row kept, undone rows
left alone:

```shell
$ python dedup.py
```

Stop the web/worker processes first so nothing writes mid-cleanup.
Re-follow after Undo is unaffected (undone rows are excluded from
both the ingest check and the cleanup).

## API

Your admin API key can be found at `config/admin_api_key.key`.

## ActivityPub API

### GET /

Returns the actor profile, with links to all the "standard" collections.

### GET /tags/:tag

Special collection that reference notes with the given tag.

### GET /stream

Special collection that returns the stream/inbox as displayed in the UI.

## User API

The user API is used by the admin UI (and requires a CSRF token when used with a regular user session), but it can also be accessed with an API key.

All the examples are using [HTTPie](https://httpie.org/).

### POST /api/note/delete{?id}

Deletes the given note `id` (the note must from the instance outbox).

Answers a **201** (Created) status code.

You can pass the `id` via JSON, form data or query argument.

#### Example

```shell
$ http POST https://your-domain.tld/api/note/delete Authorization:'Bearer <token>' id=http://your-domain.tld/outbox/<note_id>/activity
```

#### Response

```json
{
    "activity": "https://your-domain.tld/outbox/<delete_id>"
}
```

### POST /api/note/pin{?id}

Adds the given note `id` (the note must from the instance outbox) to the featured collection (and pins it on the homepage).

Answers a **201** (Created) status code.

You can pass the `id` via JSON, form data or query argument.

#### Example

```shell
$ http POST https://your-domain.tld/api/note/pin Authorization:'Bearer <token>' id=http://your-domain.tld/outbox/<note_id>/activity
```

#### Response

```json
{
    "pinned": true
}
```

### POST /api/note/unpin{?id}

Removes the given note `id` (the note must from the instance outbox) from the featured collection (and un-pins it).

Answers a **201** (Created) status code.

You can pass the `id` via JSON, form data or query argument.

#### Example

```shell
$ http POST https://your-domain.tld/api/note/unpin Authorization:'Bearer <token>' id=http://your-domain.tld/outbox/<note_id>/activity
```

#### Response

```json
{
    "pinned": false
}
```

### POST /api/like{?id}

Likes the given activity.

Answers a **201** (Created) status code.

You can pass the `id` via JSON, form data or query argument.

#### Example

```shell
$ http POST https://your-domain.tld/api/like Authorization:'Bearer <token>' id=http://activity-iri.tld
```

#### Response

```json
{
    "activity": "https://your-domain.tld/outbox/<like_id>"
}
```

### POST /api/boost{?id}

Boosts/Announces the given activity.

Answers a **201** (Created) status code.

You can pass the `id` via JSON, form data or query argument.

#### Example

```shell
$ http POST https://your-domain.tld/api/boost Authorization:'Bearer <token>' id=http://activity-iri.tld
```

#### Response

```json
{
    "activity": "https://your-domain.tld/outbox/<announce_id>"
}
```

### POST /api/block{?actor}

Blocks the given actor, all activities from this actor will be dropped after that.

Answers a **201** (Created) status code.

You can pass the `id` via JSON, form data or query argument.

#### Example

```shell
$ http POST https://your-domain.tld/api/block Authorization:'Bearer <token>' actor=http://actor-iri.tld/
```

#### Response

```json
{
    "activity": "https://your-domain.tld/outbox/<block_id>"
}
```

### POST /api/follow{?actor}

Follows the given actor.

Answers a **201** (Created) status code.

You can pass the `id` via JSON, form data or query argument.

#### Example

```shell
$ http POST https://your-domain.tld/api/follow Authorization:'Bearer <token>' actor=http://actor-iri.tld/
```

#### Response

```json
{
    "activity": "https://your-domain.tld/outbox/<follow_id>"
}
```

### POST /api/new_note{?content,reply}

Creates a new note. `reply` is the IRI of the "replied" note if any.

Answers a **201** (Created) status code.

You can pass the `content` and `reply` via JSON, form data or query argument. To attach an image, POST `multipart/form-data` with a `file` field (EXIF data is stripped, thumbnails are generated).

#### Example

```shell
$ http POST https://your-domain.tld/api/new_note Authorization:'Bearer <token>' content=hello
```

#### Response

```json
{
    "activity": "https://your-domain.tld/outbox/<create_id>"
}
```

### POST /api/undo{?id}

Undoes a previous outbox activity (like, boost, follow...). `id` is the IRI of the activity to undo, or its short outbox id.

Answers a **201** (Created) status code.

#### Example

```shell
$ http POST https://your-domain.tld/api/undo Authorization:'Bearer <token>' id=https://your-domain.tld/outbox/<like_id>
```

#### Response

```json
{
    "activity": "https://your-domain.tld/outbox/<undo_id>"
}
```


### GET /api/stream


#### Example

```shell
$ http GET https://your-domain.tld/api/stream Authorization:'Bearer <token>'
```

#### Response

```json
```


## Contributions

PRs are welcome, please open an issue to start a discussion before your start any work.
