# micronote.pub Migration Plan

Goal: modernize the codebase and swap components with minimal changes to
`NeoSQLite` / `active-boxes` (both are our own projects; app-side
modernization beats adding legacy shims to the libraries).

Decisions (agreed):

- Database: MongoDB → `NeoSQLite==1.16.1` (single SQLite file, WAL; covers our backlog: `find()` option kwargs, TTL expiry, `watch()` resume + `$match` filtering, ObjectId ordering — see `../neosqlite/documents/releases/`).
- ActivityPub: `little-boxes` → `active-boxes==0.2.3` (async-first, use `*_sync` wrappers in Flask; backend `fetch_iri` override is `async` to match the base).
- CSS: drop Sass/`libsass`, ship plain CSS with custom properties.
- Tasks: drop Celery/RabbitMQ, replace with a `watch()`-driven `jobs` collection + single worker process.
- Everything else: upgrade to latest that still supports Python 3.11 (Flask 3.x, etc.).
- `PyMongo`/`GridFS` usage is modernized **in this repo** — no deprecated-API shims will be added to `NeoSQLite` (see `../neosqlite/documents/todo/`).

## Language target & style (applies to every phase)

- **Target Python 3.11 exactly.** It ships on both RHEL8 and RHEL9, so it is a good portable milestone (not necessarily the deploy OS). `Dockerfile` uses `python:3.11-slim`; CI tests 3.11; `requires-python` allows `>=3.11` but code must not need anything newer.
- **Modernize syntax up to 3.11 — never beyond.** Allowed: `X | Y` unions, `match`, `tomllib`, `datetime.timezone.utc`, `str.removeprefix/removesuffix`, dataclasses/`functools.cache`. Forbidden: 3.12+-only syntax (`type` statements, nested same-quote f-strings, `override`, etc.).
- **Never use `lambda`.** Always a named `def` — grep must return zero hits at the end.
- **Readability is the top priority**, above brevity or cleverness. Prefer explicit code a future-you (6 years later) can understand at a glance.

## Phase 0 — Baseline (do first)

1. Freeze behavior: `cp -r tests/fixtures/me.yml config/me.yml`, run `integration_test.py` + `federation_test.py` green on the current stack.
2. Record current `requirements.txt` versions for the upgrade diff.

## Phase 1 — Modernize PyMongo usage (no behavior change, still on MongoDB)

Replace every deprecated call with its modern equivalent:

| Old | New | Sites |
| :--- | :--- | :--- |
| `Collection.count(q)` | `count_documents(q)` | `admin.py:49,54,57,64`, `app.py:116,139,140,286,497,915`, `api.py:238,239` |
| `find(q).count()` | `count_documents(q)` | `app.py:109,113` (`inject_config`) |
| `Collection.remove(...)` | `delete_many(...)` | `tasks.py` `invalidate_cache` (x6) + `finish_post_to_outbox:425`, `migrations.py:38` |
| `Collection.update(filter, update)` | `update_many(filter, update)` | `activitypub.py:383,407,504` |
| `find(q, limit=N)` | keep (supported natively since NeoSQLite 1.16.0) | `activitypub.py:605`, `utils/query.py` |
| `DB.client.close()` per call | shared client, close on teardown | `activitypub.py:95`, `gen_feed/json_feed/build_inbox_json_feed` |

Verify: full test suite green on MongoDB before touching the driver.

## Phase 2 — Modernize GridFS usage (still on MongoDB)

Rewrite `utils/media.py` (`MediaCache`) from legacy `gridfs.GridFS` to `GridFSBucket`:

- `put(buf, url=, size=, kind=, content_type=, upload_filename=)` → `upload_from_stream(filename, buf, metadata={"url":..., "kind":..., "size":...})`.
- `find_one({url, size, kind})` → `find({"metadata.url":..., "metadata.kind":..., "metadata.size":...})`.
- Reads: `f.content_type / f.length / f.md5 / f.uploadDate` → `GridOut` modern attrs (`upload_date`); update `app.py:serve_media/serve_uploads` headers and `filters.py:_get_file_url` (`str(doc._id)` stays).
- Media policy (post-migration): all cached/uploaded images are re-encoded to WebP (quality 85) with EXIF orientation applied and alpha preserved; non-images stay gzipped. Serving sniffs gzip magic, so pre-WebP entries need no migration. Uploaded images take a `.webp` extension in both storage and URL; old URLs keep serving since the route looks up by id only.
- Requires NeoSQLite ≥ 1.16.2, whose `GridOutCursor` filters dotted `metadata.*` correctly (1.16.1 silently returned the whole bucket).

Verify: upload/serve round-trip + admin media views green on MongoDB.

## Phase 3 — MongoDB → NeoSQLite swap

1. `config.py`:
   - Replace `MongoClient`/`create_db_client` with one shared `neosqlite.Connection("data/microblogpub.db", journal_mode="WAL", ttl_sweep_interval_s=60)`.
   - `GRIDFS` → `GridFSBucket(conn.db)`; `MEDIA_CACHE = MediaCache(bucket, ...)`.
   - `create_indexes()`: keep all `activities`/`translate` indexes; `cache2` TTL (`expireAfterSeconds=43200`) now works natively (declared TTL + lazy auto-purge on `find`/`find_one` + opt-in background sweeper; `purge_expired()` for tests).
   - `_drop_db()` → `Connection.drop_database()` (new in 1.16.0; debug only).
2. `activitypub.py` (`MicroblogPubBackend`): stop opening/closing a client per instance; use the shared connection. `paginated_query` + `build_ordered_collection` keep `_id`/`ObjectId` pagination (verify `str` ↔ `ObjectId` round-trip).
3. Data migration: one-off `scripts/mongo_to_sqlite.py` (dump Mongo JSON preserving `_id`, `insert_one` into SQLite, `create_indexes()`).
4. Volumes: `data/mongodb`, `data/rabbitmq` → single `data/*.db` (+WAL).

Verify: integration tests green on SQLite; compare a Mongo dump vs SQLite dump for the same fixture flow.

## Phase 4 — little-boxes → active-boxes 0.2.1

- Imports: `little_boxes` → `active_boxes` (`config.py`, `activitypub.py`, `app.py`, `api.py`, `admin.py`, `filters.py`, `migrations.py`, `utils/lookup.py`, `dedup.py`); `strtobool` now top-level; `Key` from `active_boxes.key`.
- Flask stays sync: `fetch_remote_activity` → `fetch_remote_activity_sync`, `get_actor/get_object` → `get_actor_sync/get_object_sync`, `get_actor_url` → `get_actor_url_sync`, `get_remote_follow_template` → `..._sync`, `parse_collection` → `parse_collection_sync` (`tests/federation_test.py` too).
- HTTP signatures (biggest risk): `HTTPSigAuth` is gone. Rewrite `tasks.py:post_to_remote_inbox` around `sign_request()` (+ RFC9421 double-knock) and `app.py:inbox` around `verify_request_sync()`. Keep our own delivery loop, dedup (`inbox_check_duplicate`), retry/backoff — the library intentionally leaves those to the app via `ActivityPubPlugin`.
- Requires Python `>=3.11` (see Language target above).

Verify: federation tests against two local instances + manual Mastodon interop.

## Phase 5 — Celery → watch() job queue

- New `jobs` collection `{type, iri/payload/to, attempts, next_run, status}`; `post_to_inbox/post_to_outbox` enqueue via `insert_one` instead of `.delay()`.
- New `worker.py`: startup drain `find({status:"pending", next_run:{"$lte":now}})` then `watch(pipeline=[{"$match": {"fullDocument.status": "pending"}}], full_document="updateLookup")` tail (both new in 1.16.0); track `stream.resume_token` and resume with `watch(resume_after=token)` across restarts (bounded 1000-row history); port the 9 task bodies (`process_new_activity`, `fetch_og_metadata`, `cache_object`, `cache_actor`, `cache_attachments`, `finish_post_to_inbox`, `finish_post_to_outbox`, `forward_activity`, `post_to_remote_inbox`) keeping `max_retries=9` exponential backoff via `attempts`/`next_run`. Recipe: `../neosqlite/examples/watch_job_queue.py` + `../neosqlite/documents/watch.md`.
- Run **one** worker process (not per-gunicorn-worker) to avoid double delivery; switch gunicorn off `gevent`.
- Delete: `Celery()` app, `celery.sh`, `flower`, `MICROBLOGPUB_AMQP_BROKER`, `rmq`/`celery` services.

Verify: inbox→outbox→delivery flow + retry-on-failure + worker-restart (no lost/double jobs beyond documented at-least-once).

Note: `process_new_activity` CREATE triage was simplified post-migration — the old public-reply forward rule was always overwritten by the inbox-forwarding checklist, so only the checklist (followers addressed + local thread) ever took effect. Removed with flag-identical results on five offline cases (plain, local reply ± followers, OStatus, gone target).

## Phase 6 — Theme + PWA consolidation (Sass → plain CSS, rebrand)

Background: this fork inherited `microblog.pub` theming plus a per-instance
trick — `static/pwa` was a symlink flipped between `static/pwa-ublog/` and
`static/pwa-c3po/`, with its own CSS variant each. That symlink is gone, so
`layout.html:8` (`/static/pwa/manifest.json`) and every icon path inside both
manifests (`/static/pwa/icon-*.png`) 404 today. Since we rebrand to
micronote.pub, consolidate instead of reviving the symlink.

1. **Plain CSS:**
   - Delete `libsass` + `config.py` `sass.compile()` block.
   - Flatten `sass/base_theme.scss` → `static/app.css`; `light/dark.scss` vars → `:root[data-theme]` custom props; inject `THEME_STYLE/COLOR` via `layout.html`.
   - `layout.html:30` inlines `{{ config.CSS }}` — replace with `<link rel="stylesheet" href="/static/app.css">` plus a tiny inline `:root` vars block (cacheable stylesheet instead of per-response `<style>`).
   - Remove Sass build deps from `Dockerfile`.
2. **Rebrand strings:** footer (`layout.html:66-75`) and `<title>` still say microblog.pub with old source-code links — point at micronote.pub.
3. **External CDN:** `layout.html:16,34` loads PureCSS 1.0.0 from unpkg (2017, integrity-pinned). Vendor it into `static/` or drop it — external CSS breaks the cacheable/offline story and needs no CDN for ~720px layout.
4. **Single PWA (no symlink):** real `static/pwa/` directory, micronote branding:
   - One `manifest.json`: `id: "/"`, `name`/`short_name` from `me.yml`, `start_url: "/"`, `display: "standalone"`, `theme_color` matching config, `description`, icons **192 + 512** with `"purpose": "any maskable"` (drop 128/144/256; regenerate from the new logo).
   - `layout.html` fixes: manifest path (already `/static/pwa/manifest.json` — keep), `apple-touch-icon` → 180px file (currently 152), add `mobile-web-app-capable` alongside the deprecated `apple-mobile-web-app-capable`, keep dynamic `theme-color`.
   - `layout.html:15` references `static/favicon.ico`, which doesn't exist (only `c3po.ico`/`ublog.ico`) — ship one `favicon.ico` (+ SVG if cheap).
   - Delete `static/pwa-ublog/`, `static/pwa-c3po/` once icons are regenerated.
5. **Optional (small):** ~20-line service worker caching `app.css`/fonts for offline reads; pages stay no-JS (SW is a separate asset, progressive enhancement only).

Verify: visual check of both themes; `curl` every PWA URL (manifest, icons, favicon); Lighthouse PWA audit installable; no `sass` in `requirements.txt`.

## Phase 7 — Deps, Docker, CI

- Breaking upgrades:
  - `itsdangerous` (`JSONWebSignatureSerializer` removed → `URLSafeTimedSerializer` in `config.py:7,182`/`api.py`/`indieauth.py`; add token expiry while there — current JWTs never expire).
  - `jinja2-htmlcompress` (dead on Jinja3 — delete the `app.py` extension branch).
  - `python-u2flib-server` → WebAuthn (`python-fido2`): the U2F second factor is doubly dead — Chrome removed the `u2f` JS API **and** `login.html`/`u2f.html` call `u2f.register()` without ever loading `u2f-api.js`, so the inline `<script>` already throws. Rewrite `app.py:u2f_register` + `admin.py:admin_login` around WebAuthn.
  - `emoji` 2.x: `use_aliases=True` removed (`utils/emoji.py:5-7` crashes on import) → `language="alias"` + custom `:blob_:` handling.
  - `passlib` (dead) → `bcrypt` directly (`admin.py:verify_pass`).
  - `opengraph` (unmaintained git dep) → pin or replace; `html5lib` is implicit via `utils/opengraph.py` + `utils/media.py` `load()`/`get()` crash on missing/non-image `content-type` (`.startswith` on `None`) — guard both.
  - Drop `tornado<6` pin, `gevent` worker (→ `gthread`), `yandex-translater` + `langdetect` fork + `similar_text` (delete `filters.py:translate()` entirely per Yandex-removal decision; also remove the per-request `DetectorFactory.seed = 0` global mutation).
  - `feedgen` (unmaintained, needs `lxml`) — keep pinned only if Atom/RSS tests pass, else render Atom manually.
  - `datetime.utcnow()` (`activitypub.py:431`) → `datetime.now(timezone.utc)` (removed in Python 3.14).
  - `tests/integration_test.py:16` `yaml.load(f)` without `Loader` → breaks on PyYAML 6+; `dev-requirements.txt` still points at `little-boxes` git URL.
- Flask 3 cleanups: `CSRFProtect(current_app)` at import in `api.py`/`admin.py` → `CSRFProtect()` + `init_app`; keep `WTF_CSRF_CHECK_DEFAULT=False` only if token flow still needs it; set `SESSION_COOKIE_SECURE/SAMESITE`, `MAX_CONTENT_LENGTH` for uploads (`api.py:new_note` currently accepts unbounded files), regenerate session on login (fixation), return 404 not 500 on bad `ObjectId` in `/media/*` + `/uploads/*`.
- `indieauth.py` hardening: `get_client_id_data()` server-fetches an arbitrary `client_id` URL with no validation (marked FIXME) — validate `redirect_uri` against the fetched client page before redirecting.
- Docker: `python:3.11-slim`, services collapse to `web + worker` + SQLite volume; drop obsolete `version:`/`links:` keys; `docker-compose-dev/-tests.yml` updated; `.travis.yml` (py3.6) → modern CI.
- `Makefile`: `update` runs `git pull` but this repo is Mercurial; image name `microblogpub:latest` and `/opt/ublog` venv path are pre-rebrand leftovers — rename to `micronote:latest` / `/opt/micronote`.
- Requirements: everything unpinned — pin (pip-tools or lockfile) at cutover.
- Lint gate (new, runs in CI before tests):
  - Python: `ruff` (replaces flake8+black+isort; single binary, set `line-length = 120` to match `setup.cfg`) + existing `mypy`.
  - HTML/Jinja templates: `djlint` (pip-installable, understands Jinja2 `{% %}` so it won't choke on `layout.html`/`utils.html`; lints obsolete tags, dup IDs, basic a11y). Run `djlint --lint` in CI, `djlint --reformat` as a one-shot during Phase 6.
  - CSS: `stylelint` + `stylelint-config-standard` (via `npx`, no Node project needed; one-shot in CI). Catches the flattened `app.css` for invalid props and dead selectors.
  - JS: almost none after the U2F removal — only the optional service worker. Cover it with `biome` (single binary, zero config) or skip the gate if no `.js` file exists.
- Tests: update imports, `docker-compose-tests.yml` (two `web` instances, no `mongo`/`rmq`/`celery`).
- Local ActivityPub matrix harness: `scripts/stub_remote.py` (fake remote node: actor/key/note serving, strict signature verification, delivery capture) + `scripts/ap_matrix.py` driver. Implemented cases: signed follow (auto-Accept + verified delivery), same-actor re-follow with new id (dropped, no second Accept, single row), signed note (stream flag, no outbound), signed like (counter), unsigned follow (fetch-fallback path), unsigned Undo + refollow-after-Undo. Remaining for the full matrix: announce/update/delete/media. Requires the peer at `MICRONOTE_DEBUG=1`; see README “Simulating a remote node locally”.
  - Outbound via API (no signatures needed): `new_note` → `like` → `undo` → `boost` → `undo` → `delete`, `follow` → `undo`, `block` → `undo`, `pin`/`unpin`; assert each lands in `/outbox` and federates (stub inbox receives the POST).
  - Outbound media via API: `new_note` with `multipart/form-data` file upload (small generated PNG + JPEG fixtures, no binary blobs in repo — generate with Pillow at runtime); assert EXIF stripped, thumbnail + original served at `/uploads/<oid>/<fname>`, attachment block present in the federated `Create`, and stub inbox receives it.
  - Inbound via `/inbox` (properly signed by the stub key): `Follow` → expect auto-`Accept` in outbox; `Create` (plain, reply, mention) → expect stream flag + thread linkage; `Like`/`Announce` on local notes → expect counters; `Update` → expect content change; `Undo` variants → expect counter revert; `Delete` → expect tombstone; duplicate redelivery → expect single processing (`inbox_check_duplicate`).
  - Inbound media: `Create` with image `attachment`s (served by the stub server) → expect `cache_attachments` to fetch + thumbnail them and the rendered note to reference cached `/media/<id>` URLs, not the remote bytes.
  - Assert side effects in DB (counters, `meta` flags) and in rendered HTML/JSON feeds, not just HTTP 201s.
  - This replaces manual Mastodon testing for regressions; keep the two-docker `federation_test.py` for release confidence only.

## Phase 7 — Packaging & Modern Layout Modernization (`pyproject.toml` + `src/` layout)

- Adopt modern PEP 621 `pyproject.toml` source of truth.
- Move application source code from flat root layout into standard `src/` layout (`src/micronote/`).
- Package discovery with `[tool.setuptools.packages.find] where = ["src"]`.
- Package data bundling for `templates/` and `static/`.
- Entry points configured via `[project.scripts]` (`micronote-worker = "micronote.worker:main"`).
- Tooling configuration consolidated into `pyproject.toml`:
  - Ruff linter configuration
  - Mypy type-checking configuration
  - Pytest runner configuration (`testpaths = ["tests"]`, `pythonpath = ["src"]`)
- Dynamic directory configuration with environment variable overrides (`MICRONOTE_CONFIG_DIR`, `MICRONOTE_DATA_DIR`).
- Absolute package imports throughout (`from micronote import ...`, `from micronote.utils ...`).
- Scripts (`scripts/ap_matrix.py`) and development workflows updated to use editable install (`pip install -e ".[dev]"`).

## Delete list (dead code, remove during migration)

- `migrations.py` blueprint + its route registration: one-shot 2018-era routes (`migration1_step1…migration5`) referencing dropped collections (`DB.outbox/inbox/replies`) and `tasks.*.delay` — dangerous to keep behind only `login_required`.
- `dedup.py`: executes DB queries **at import time**; convert to a `__main__` script or delete (d everyday dupes are prevented by `inbox_check_duplicate`).
- `celery.sh`, `ublog.sh`, `ublog.screenrc` (pre-Docker venv workflow; env vars point at `ublog:ublog@127.0.0.1`).
- `utils/headers.py` mutable default `headers={}` (fix if kept).
- `filters.py:translate()` + Yandex config keys (see above).

## Rebrand checklist (microblog.pub → micronote.pub)

- [ ] Env vars `MICROBLOGPUB_*` → `MICRONOTE_*` (`config.py:61,117`, `tasks.py:32`, all compose files, `*.sh`, README).
- [ ] `app.py:136` template var `microblogpub_version`, `:150` `X-Powered-By: microblog.pub`, `:491,499` nodeinfo `name`/`sourceCode` (still `tsileo/microblog.pub`), `config.py:111` User-Agent `(microblog.pub/...)`, `activitypub.py:573` feed text.
- [ ] Templates footer/title (`layout.html:7,11,68-70`), `static/*.ico` naming, PWA names (Phase 6).
- [ ] `README.md`, `Dockerfile`/`Makefile`/compose image names, `tests/fixtures/*/config` contents.

## Rollback notes

- Keep a Mongo dump before cutover; each phase merges separately in order 1→7 so any phase can be reverted without losing the previous ones.
