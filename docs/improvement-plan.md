---
type: improvement_plan
title: Separation of Concerns Improvement Plan
description: Phased, independently shippable refactoring plan to untangle module boundaries, caching, presentation, and data access in micronote.pub.
resource: src/micronote/
status: draft
sources:
  - src/micronote/app.py
  - src/micronote/activitypub.py
  - src/micronote/worker.py
  - src/micronote/tasks.py
  - src/micronote/config.py
  - src/micronote/filters.py
  - src/micronote/api.py
  - src/micronote/admin.py
verified: machine-confirmed
stale_after: 2027-09-20T00:00:00Z
tags: [architecture, refactoring, separation-of-concerns, plan, maintainability]
timestamp: 2026-09-20T00:00:00Z
---

# Separation of Concerns Improvement Plan

This plan removes the layering problems found in the 2026-09-20 architecture
audit. Each phase is **behavior-preserving** and **independently landable**:
a developer can complete exactly one phase, ship it, and leave the rest for
later. Dependencies only ever point to earlier phases.

The ordering puts correctness risks and structural enablers first:

1. Boundaries and import cycles (Phase 1) unblock every later phase.
2. Cache ownership (Phase 2) closes the highest correctness risk class.
3. `app.py` decomposition (Phases 3–4) removes the biggest structural debt.
4. Layer purity and data access (Phases 5–6) finish the HTTP/domain split.
5. Worker and config cleanups (Phases 7–8) are lower risk and can be skipped.

---

## Metadata Summary

| Attribute | Value |
| --- | --- |
| **Audit basis** | Architecture audit of 2026-09-20 (separation of concerns) |
| **Target** | `src/micronote/` (application code and tests) |
| **Landing model** | One Mercurial topic per phase (`hg topic soc-phase-N-...`) |
| **Behavior contract** | No URL, API, federation, or UI change in any phase |
| **Related documents** | [Defect Registry](./bugs/index.md), [Migration Plan](./migration.md) |

---

## Goals

- One owner for page cache reads, writes, and invalidation.
- An acyclic module graph with no cross-module private imports.
- Routes split by delivery mechanism (HTML vs ActivityPub) instead of
  `if is_api_request()` branches.
- Data access behind named query functions instead of raw collection calls
  in route handlers.
- No database writes or job enqueues from template rendering.
- Job queue mechanics separated from job handler business logic.
- No key generation, subprocesses, or service construction at import time.

## Non-Goals

- No new features, no API or URL changes, no database schema changes.
- No framework swap (Flask stays, NeoSQLite stays, active-boxes stays).
- No reformatting sprees; diffs stay reviewable and scoped to the phase.
- No behavior "fixes" smuggled into refactors. Bugs found on the way are
  filed under [docs/bugs/](./bugs/index.md) and fixed separately.

---

## Per-Phase Contract

Every phase must satisfy all of the following before it is considered done:

1. **Behavior preserving.** URL map, API responses, federation flows, and
   rendered pages are unchanged. The Phase 0 guard tests prove this.
2. **Lint clean.** `make lint` passes (ruff + mypy + djlint).
3. **Tests green.** `pytest` passes; tests are updated for moved code and
   patch targets, never deleted to make a refactor pass.
4. **Smoke tested.** Eager-mode server boots, a note can be posted, the
   homepage renders, and the worker drains a job when the phase touches
   those paths. `make lint-web` when templates or rendered HTML change
   (requires resolving the baseline stylelint issue in `app.css:183`).
5. **One concern.** The phase does not mix a refactor with a behavior change.
   If it grows, split it instead of expanding scope.
6. **Tracked.** The phase's status is updated in the
   [Phase Status](#phase-status) table of this document.

### Verification Quick Reference

```shell
make lint                  # ruff + mypy + djlint
pytest                     # test suite (federation tests excluded by config)
make lint-web              # renders fixtures, stylelint + html-validate (Node)

# Eager dev server (no worker needed)
MICRONOTE_TASK_EAGER=1 FLASK_DEBUG=1 MICRONOTE_DEBUG=1 \
  FLASK_APP=micronote.app flask run -p 5005 --with-threads

# Federation harness (separate terminals)
python scripts/stub_remote.py --port 5006
python scripts/ap_matrix.py all
```

---

## Phase Map

| Phase | Focus | Criticality | Depends on | Size | Status |
| --- | --- | --- | --- | --- | --- |
| [0](#phase-0--baseline--structural-guardrails) | Baseline & structural guardrails | Prep | — | S | Not started |
| [1](#phase-1--module-boundaries--import-cycles) | Module boundaries & import cycles | Critical | 0 | S | Not started |
| [2](#phase-2--cache-ownership--invalidation) | Cache ownership & invalidation | Critical | 1 | M | Not started |
| [3](#phase-3--decompose-apppy-into-blueprints) | Decompose `app.py` into blueprints | High | 1 | M | Not started |
| [4](#phase-4--separate-html-and-activitypub-responses) | HTML vs ActivityPub response separation | High | 3 | M | Not started |
| [5](#phase-5--data-access-layer--flask-free-helpers) | Data-access layer & Flask-free helpers | High | 3, 4 | L | Not started |
| [6](#phase-6--media-resolution-out-of-template-filters) | Media resolution out of template filters | Medium | 1 | S | Not started |
| [7](#phase-7--worker-queue-runner-vs-job-handlers) | Worker: queue runner vs job handlers | Medium | 1 | M | Not started |
| [8](#phase-8--deferred-key-material--version) | Deferred key material & version | Medium | 1 | M | Not started |

Sizes are rough: **S** ≤ 1 day, **M** = 1–3 days, **L** = 3–5 days.

Dependencies are soft where noted: if a dependency phase was skipped, do the
same work in the current file layout and leave a note in the phase status.

---

## Phase 0 — Baseline & Structural Guardrails

### Why

- Later phases move code across modules. Existing tests cover behavior, not
  structure, so a refactor could silently change the URL map or import graph.
- The bug registry ([docs/bugs/](./bugs/index.md)) shows regressions cluster
  around cache invalidation and module-init duplication (BUG-023, BUG-024).

### Steps

1. Record the baseline: run `make lint` and `pytest`, confirm green, and note
   the pass count in the [Phase Status](#phase-status) table. Resolve the
   pre-existing stylelint failure in `src/micronote/static/app.css:183`
   (`padding: 5px 5px` → `5px`) so `make lint-web` passes as well.
2. Add `tests/test_url_map.py`: snapshot the sorted `(rule, methods)` pairs
   from `app.url_map`. **Do not snapshot endpoint names** — they change when
   routes move into blueprints in Phase 3.
3. Add `tests/test_imports.py`: spawn fresh Python processes importing
   `micronote.app`, `micronote.tasks`, and `micronote.worker`, and assert
   exit code 0. This catches import cycles introduced later.
4. Add `tests/test_response_contract.py`: pin the content negotiation
   contract for dual routes (`/`, `/note/<id>`, `/followers`, `/following`,
   `/tags/<tag>`, `/liked`) for both HTML and `application/activity+json` Accept
   headers, and assert HTTP 404 for browser HTML requests on ActivityPub-only
   routes (`/outbox`, `/featured`, `/inbox`). Phase 4 must not change this behavior.
5. Extend cache contract coverage if not already present in
   `tests/test_pin_cache_invalidation.py`: anonymous homepage is cached, and
   posting to the outbox clears it.

### Files

- `src/micronote/static/app.css` (fix redundant padding shorthand)
- `tests/test_url_map.py` (new)
- `tests/test_imports.py` (new)
- `tests/test_response_contract.py` (new)
- `tests/test_pin_cache_invalidation.py` (extend, optional)

### Verification

- `pytest` green; `make lint` green; `make lint-web` green.
- Deliberately rename a rule in a scratch branch and confirm
  `tests/test_url_map.py` fails (sanity check of the guard).

### Exit Criteria

- Baseline results are recorded (`pytest`, `make lint`, `make lint-web` all green).
- URL map, import, and response contract guards are committed and passing.

### Dependencies

None. This phase only adds tests.

---

## Phase 1 — Module Boundaries & Import Cycles

### Why

- Import cycles are papered over with function-level imports:
  `tasks.py:41` (imports `worker`) and `activitypub.py:654` (imports `tasks`).
- `worker.py:30-39` imports `MY_PERSON`, `back`, job statuses, and `log`
  through `tasks` re-exports instead of the modules that define them.
- Private helpers are imported across module boundaries:
  - `_build_thread` — `app.py:46`, `admin.py:25`, `tests/test_thread.py`
  - `api._api_required` — `app.py:717`, `app.py:877`
  - `config._drop_db` — `api.py:27`
  - `activitypub._actor_to_meta` / `_safe_object_actor_meta` — `worker.py:201,294,302`
  - `_COUNTS_CACHE` — `tests/test_inject_config.py`, `tests/test_drop_cache.py`
- `tasks.py` imports `activitypub` only for the `Box` enum, creating a
  logical cycle with the backend's `post_to_outbox` delegation
  (`activitypub.py:653-656`).
- Dead code: `set_post_to_remote_inbox` (`activitypub.py:360`), a commented
  actor-cache block (`activitypub.py:311-316`), a commented error handler
  (`app.py:213-220`).

### Steps

1. Create `src/micronote/jobs.py` and move from `tasks.py`: `STATUS_PENDING`,
   `STATUS_PROCESSING`, `STATUS_FAILED`, `MAX_RETRIES`, `TASK_EAGER`, and
   `enqueue_job()`. Keep the eager-mode drain as a documented function-level
   import of `worker.drain_jobs` — this is the only intentional back
   reference, and the module-level graph stays acyclic.
2. Create `src/micronote/boxes.py` and move the `Box` enum there from
   `activitypub.py:141-144`. Update importers (`activitypub`, `tasks`, `app`,
   `admin`, `api`, `dedup`, tests). This removes the `tasks → activitypub`
   module edge.
3. With `tasks.py` no longer importing `activitypub`, promote the function-level
   import `from micronote.tasks import post_to_outbox` in `activitypub.py:654`
   (`MicroblogPubBackend.post_to_outbox`) to a standard top-level module import.
   Update `tasks.py` to import `enqueue_job` from `jobs`. Update
   `filters.py:104` and `worker.py` likewise. Update tests that patch
   `micronote.tasks.enqueue_job` to patch `micronote.jobs.enqueue_job`
   (`tests/test_filters.py:149,169,181,218`).
4. Update `worker.py` imports: statuses/`enqueue_job`/`MAX_RETRIES` from
   `jobs`; `MY_PERSON`/`back` from `instance`; a local module logger instead
   of `tasks.log`.
5. Make legitimate cross-module helpers public:
   - `utils/thread.py`: `_build_thread` → `build_thread`
   - `api.py`: `_api_required` → `require_api_auth`
   - `config.py`: `_drop_db` → `drop_db`
   - `activitypub.py`: `_actor_to_meta` → `actor_to_meta`,
     `_safe_object_actor_meta` → `safe_object_actor_meta`
6. Delete the dead code listed under **Why**.
7. Tighten `tests/test_imports.py`: importing `micronote.app` must not import
   `micronote.worker` (the worker is a separate process).

### Files

- `src/micronote/jobs.py` (new), `src/micronote/boxes.py` (new)
- `src/micronote/tasks.py`, `worker.py`, `activitypub.py`, `config.py`,
  `api.py`, `app.py`, `admin.py`, `filters.py`, `utils/thread.py`
- `tests/test_filters.py`, `tests/test_thread.py`, `tests/test_imports.py`

### Verification

- `pytest`, `make lint`.
- `python -c "import micronote.worker"` and
  `python -c "import micronote.app"` in fresh processes.
- Eager smoke: post a note through `/api/new_note` and confirm the outbox
  job chain completes.

### Exit Criteria

- No module-level import cycles remain.
- `rg "from micronote\..* import _" src/` returns no cross-module hits.
- Dead code from the list is gone; app and worker boot.

### Dependencies

Phase 0 (import and URL guards).

---

## Phase 2 — Cache Ownership & Invalidation

### Why

- Page cache invalidation is scattered across ten call sites:
  `tasks.py:72,77,80,84,96`, `api.py:166,180`, `worker.py:476`,
  `app.py:371`.
- The read path (`_get_cached` / `_cache`, `app.py:376-401`) and the counts
  memo (`_COUNTS_CACHE`, `app.py:85-133`) live in the route module.
- This exact drift caused BUG-023 (pin/unpin served a stale homepage for up
  to 12 hours).

### Steps

1. Create `src/micronote/cache.py` (Flask-free domain module):
   - `get_page(path, type_="html", arg=None, authenticated=False)` and
     `set_page(path, data, type_="html", arg=None, authenticated=False)` with
     the `CACHING` switch moved here from `app.py:376`. Explicit parameters keep
     `cache.py` decoupled from Flask's `request` and `session` globals.
   - Support both `"html"` and `"api"` cache types (preserving the cached API
     response for `/nodeinfo` at `app.py:512,541`).
   - `invalidate_for_activity(activity)` — move the policy from
     `tasks.invalidate_cache` (`tasks.py:70-84`) verbatim.
   - `clear()` — clears the page cache (`DB.cache2`) and the counts memo together, so
     writers can never invalidate only half the state.
2. Create `src/micronote/stats.py`: move `_get_counts` queries
   (`app.py:88-133`) and the TTL memo (`_COUNTS_CACHE`); expose `counts()`
   and `clear_counts()`. `cache.clear()` calls `stats.clear_counts()`.
3. Replace all call sites with the new API:
   - `tasks.py`: `invalidate_cache` delegates to
     `cache.invalidate_for_activity`; `post_to_outbox` calls `cache.clear()`.
   - `api.py:166,180`, `worker.py:476`, `app.py:371-372` call `cache.clear()`.
   - `app.py` read path uses `cache.get_page` / `cache.set_page` (passing
     `request.path` and `bool(session.get("logged_in"))`); `/nodeinfo` uses
     `type_="api"`; `inject_config` uses `stats.counts()`.
4. Update tests: `tests/test_inject_config.py`, `tests/test_drop_cache.py`
   (`_COUNTS_CACHE` imports), `tests/test_pin_cache_invalidation.py`, and the
   `micronote.worker.tasks.invalidate_cache` patch target in
   `tests/test_announce_handling.py:94`.

### Files

- `src/micronote/cache.py` (new), `src/micronote/stats.py` (new)
- `src/micronote/app.py`, `tasks.py`, `api.py`, `worker.py`
- `tests/test_inject_config.py`, `tests/test_drop_cache.py`,
  `tests/test_pin_cache_invalidation.py`, `tests/test_announce_handling.py`

### Verification

- `rg "cache2.delete_many" src/` matches only `cache.py`.
- `pytest`, `make lint`.
- Manual: request the homepage twice (second logs "from cache"), post a note,
  request again (fresh render).

### Exit Criteria

- One module owns every cache write and invalidation decision.
- Counts memo lives in `stats.py`; tests green.

### Dependencies

Phase 1 (`jobs.py` exists; `worker.py` imports are clean).

---

## Phase 3 — Decompose `app.py` into Blueprints

### Why

- `app.py` is 1087 lines mixing the app factory, logging setup, error
  handlers, media serving, auth endpoints, ActivityPub protocol endpoints,
  HTML views, and serialization adapters (`app.py:48-1087`).
- Every later phase pays a tax for this file. Splitting it first is a
  mechanical, behavior-preserving move; the content-negotiation cleanup
  (Phase 4) then happens in small, reviewable modules.

### Steps

1. Create `src/micronote/web.py` with the shared HTTP helpers currently in
   `app.py`: rename local `jsonify` (`app.py:161`) to `activity_json()`,
   move `is_api_request()` (`170`) and `wants_html()` (`178`). Both the
   factory and the blueprints import from here (no cycles).
2. Create `src/micronote/ap_serialize.py` and move the serialization
   adapters (`add_extra_collection`, `remove_context`, `activity_from_doc*`,
   `activity_without_context`, `app.py:640-691`) and the collection builders
   (`embed_collection`, `simple_build_ordered_collection`,
   `build_ordered_collection`, `activitypub.py:780-865`).
3. Move the feed builders (`gen_feed`, `_feed_item`, `json_feed`,
   `build_inbox_json_feed`, `activitypub.py:659-777`) into
   `src/micronote/feeds.py`, next to the feed routes.
4. Move route groups into blueprints, preserving rules, methods, and
   behavior:
   - `views.py`: `index`, `with_replies`, `note_by_id`, `tags`,
     `followers`, `following`, `liked`, `drop_cache` (debug-only POST).
   - `ap_routes.py`: `outbox`, `outbox_detail`, `outbox_activity`,
     `outbox_activity_replies`, `outbox_activity_likes`,
     `outbox_activity_shares`, `inbox`, `featured`.
   - `wellknown.py`: `robots_txt`, `nodeinfo`, `wellknown_nodeinfo`,
     `wellknown_webfinger`, `pwa_manifest`.
   - `media_routes.py`: `serve_grid_file`, `serve_media`, `serve_uploads`,
     `favicon`.
   - `auth_views.py`: `remote_follow`, `authorize_follow`,
     `webauthn_register`.
5. Keep `app.py` as the factory: Flask creation, config update, CSRF,
   blueprint registration, error handlers, context processor,
   `after_request`.
6. Update affected call sites and test targets:
   - `app.py:492` becomes `url_for("ap.outbox_activity", item_id=note_id)`.
     (Template `url_for` calls are already blueprint-qualified and unchanged).
   - Update patch targets in `tests/test_drop_cache.py` from `micronote.app.*`
     to `micronote.views.*`.

### Files

- `src/micronote/web.py`, `ap_serialize.py`, `views.py`, `ap_routes.py`,
  `wellknown.py`, `media_routes.py`, `auth_views.py` (new)
- `src/micronote/app.py`, `activitypub.py`, `feeds.py`

### Verification

- `flask routes` output (rules and methods) is byte-identical before and
  after; `tests/test_url_map.py` passes.
- `pytest`, `make lint`, `make lint-web`.
- Eager smoke: homepage, note page, outbox, media, feeds.

### Exit Criteria

- `app.py` is roughly ≤ 200 lines and contains only factory wiring.
- Every route lives in a blueprint; the URL map is unchanged.

### Dependencies

Phase 1 (public helper names, `boxes.py`). If Phase 1 was skipped, do the
same moves in place and keep the private names until Phase 1 lands.

---

## Phase 4 — Separate HTML and ActivityPub Responses

### Why

- Twelve content-negotiation and Accept-filtering branches sit inside handler bodies:
  `app.py:405,491,697,762,797,835,874,946,971,1009,1044,1059`.
- Dual-purpose routes mix two delivery mechanisms, and page caching is
  applied ad hoc to the HTML branch only (`app.py:407-442`).
- ActivityPub-only protocol routes (`/outbox`, `/featured`, `/inbox`, `/outbox/<id>/*`)
  mix 404 rejection logic for browser requests directly within handler bodies.

### Steps

1. Add delivery decorators to `web.py` (or a new `web/negotiation.py`):
   - `negotiate(*, html, activitypub)` for true dual-representation routes:

     ```python
     def negotiate(*, html, activitypub):
         """Route to the HTML or ActivityPub handler based on the Accept header."""
         @wraps(html)
         def view(**kwargs):
             if is_api_request():
                 return activitypub(**kwargs)
             return html(**kwargs)
         return view
     ```

   - `@activitypub_only` for protocol endpoints that reject browser/HTML traffic with 404:

     ```python
     def activitypub_only(view_func):
         """Reject non-ActivityPub requests with HTTP 404."""
         @wraps(view_func)
         def view(**kwargs):
             if not is_api_request():
                 abort(404)
             return view_func(**kwargs)
         return view
     ```

2. Wire routes cleanly by delivery type:
   - **Dual representation routes**: `/`, `/note/<note_id>`, `/followers`,
     `/following`, `/tags/<tag>`, `/liked`. Split each into two named handlers
     (`*_html` and `*_ap`) registered through `negotiate(*, html=..., activitypub=...)`.
   - **ActivityPub-only routes**: `/outbox` (GET collection, POST submission),
     `/featured`, `/inbox` (GET collection, POST delivery), and `/outbox/<id>/*`
     sub-collections. Decorate these with `@activitypub_only` instead of passing
     dummy 404 HTML handlers to `negotiate`.
3. Add a `@page_cache(type_="html")` decorator (backed by `cache.py`) that derives
   its cache key from `request.path` plus pagination args (`older_than`, `newer_than`),
   bypassing when authenticated. Ensure it supports `type_="api"` for the cached
   `/nodeinfo` endpoint. Remove per-route manual `cache.get_page` / `cache.set_page` calls.
4. Keep `wants_html()` in the error handlers unchanged.

### Files

- `src/micronote/web.py` (extend) or `src/micronote/web/negotiation.py` (new)
- `src/micronote/views.py`, `ap_routes.py`, `wellknown.py`,
  `media_routes.py`, `auth_views.py`

### Verification

- `tests/test_response_contract.py` and `tests/test_url_map.py` pass.
- Cache behavior tests pass; manual check that AP responses are never cached
  and HTML responses are.
- `pytest`, `make lint`, `make lint-web`.

### Exit Criteria

- No route body contains `if is_api_request()`.
- Each dual route is two named handlers; HTML caching is a decorator.

### Dependencies

Phase 3 (routes in blueprints) and Phase 2 (cache module). If Phase 3 was
skipped, apply the split inside `app.py`; if Phase 2 was skipped, keep the
existing cache helpers behind the decorator.

---

## Phase 5 — Data-Access Layer & Flask-Free Helpers

### Why

- The same outbox lookup is repeated in seven places:
  `app.py:494,731,745,767,802,840` and `api.py:192`.
- Counts queries are duplicated between `stats.py` and `admin.py:66-93`;
  notification queries are inline in `admin.py:231-268`.
- `utils/query.py` imports Flask `request`/`abort` (`utils/query.py:1`) and
  `utils/thread.py` uses `current_app` (`utils/thread.py:1`), so pagination
  and thread building cannot be reused by the worker or scripts.

### Steps

1. Create `src/micronote/repository.py` with named query functions, for
   example: `outbox_item(item_id)`, `outbox_page(older_than, newer_than,
   limit)`, `inbox_page(...)`, `followers()`, `following()`,
   `tag_notes(tag)`, `pinned_notes()`, `notification_page(...)`,
   `activity_by_object_id(oid)`, and `counts()` (moved from `stats.py`).
2. Move `paginated_query` (`utils/query.py`) into the repository and change
   its signature to accept explicit `older_than` / `newer_than` values,
   returning `(items, older_than, newer_than)` without reading the request.
   Raise `ValueError("Invalid cursor")` on unparseable cursor strings rather than
   calling Flask's `abort(400)`. Flask's existing `@app.errorhandler(ValueError)`
   in `app.py:183` already maps `ValueError` to HTTP 400, preserving error
   responses while keeping the repository Flask-free. Call sites pass `request.args`.
3. Move `build_thread` from `utils/thread.py` to
   `src/micronote/threads.py` and replace `current_app.logger` with a module
   logger. Update `views.py`, `admin.py`, and `tests/test_thread.py`.
4. Migrate route modules, `admin.py`, and `api.py` to the repository
   functions. Leave `MicroblogPubBackend` callbacks unchanged (the backend
   contract is owned by active-boxes).
5. Delete `utils/query.py` and `utils/thread.py` once empty.

### Files

- `src/micronote/repository.py` (new), `src/micronote/threads.py` (new)
- `src/micronote/views.py`, `ap_routes.py`, `admin.py`, `api.py`,
  `stats.py`, `utils/query.py` (delete), `utils/thread.py` (delete)
- `tests/test_thread.py`, `tests/test_collections.py`

### Verification

- `pytest` including pagination and thread tests; `make lint`.
- `rg "DB\.activities\.(find|find_one|count_documents)" src/micronote/{views,ap_routes,admin,api}.py`
  returns no hits.
- Eager smoke: paginated homepage, note thread page, admin notifications.

### Exit Criteria

- Route modules contain no raw activity queries.
- Pagination and thread building are importable without a Flask context.

### Dependencies

Phases 3 and 4 (routes live in their own modules). If skipped, migrate the
call sites in `app.py` directly.

---

## Phase 6 — Media Resolution Out of Template Filters

### Why

- Template rendering performs database reads and enqueues background jobs:
  `_get_file_url` (`filters.py:114`) → `_enqueue_media_cache`
  (`filters.py:82`) → `enqueue_job`, with two module caches
  (`filters.py:28-29`).
- Media caching policy and formatting filters are unrelated concerns.
  Moving this logic to `media_urls.py` isolates media discovery and job
  mutations from template presentation. Jinja filter wrappers delegate to
  `media_urls.py` at render time, preserving on-demand caching semantics
  without tangling formatting filters with database dependencies.

### Steps

1. Create `src/micronote/media_urls.py` and move `_get_file_url`,
   `_enqueue_media_cache`, `_GRIDFS_CACHE`, and `_PENDING_CACHE_JOBS` there.
   Expose `actor_icon_url(url, size)`, `attachment_url(url, size)`,
   `og_image_url(url, size)`, and `custom_emoji_url(url)`.
2. Reduce the filters in `filters.py:156-180` to thin wrappers that call the
   resolver, preserving template behavior and return values.
3. Import `enqueue_job` from `jobs` (Phase 1).
4. Update `tests/test_filters.py` imports and patch targets
   (`_GRIDFS_CACHE`, `_PENDING_CACHE_JOBS`, `_enqueue_media_cache`,
   `micronote.tasks.enqueue_job`).

### Files

- `src/micronote/media_urls.py` (new)
- `src/micronote/filters.py`
- `tests/test_filters.py`, `tests/test_media_worker.py` (if patch targets move)

### Verification

- `pytest`, `make lint`, `make lint-web`.
- Manual: a page with a remote avatar and an attachment resolves cached
  media and enqueues at most one job per URL.

### Exit Criteria

- `filters.py` contains no database writes and no job enqueueing (pure formatting wrappers).
- All media caching policy and queue mutations live in `media_urls.py`.

### Dependencies

Phase 1 (`jobs.py`). Independent of Phases 3–5.

---

## Phase 7 — Worker: Queue Runner vs Job Handlers

### Why

- `worker.py` is 747 lines: queue mechanics (`581-747`) mixed with ten
  domain handlers (`50-565`), plus duplicated media caching between
  `cache_object` (`185-257`) and `cache_attachments` (`341-403`).
- Handlers can only be tested through the worker module, and queue changes
  require reading domain code (and vice versa).

### Steps

1. Create `src/micronote/handlers.py` and move the ten handler functions and
   the `JOB_HANDLERS` map (`worker.py:50-578`) there verbatim.
2. Keep `worker.py` for queue mechanics only: `retry_delay`, `run_job`,
   `drain_jobs`, resume-token helpers, the watch loop, and `main`.
3. Extract a shared helper for caching an object's emojis, attachments, and
   actor icon, and use it from both `cache_object` and `cache_attachments`.
4. Update test imports and patch targets: `tests/test_cache_object.py`,
   `tests/test_announce_handling.py`, `tests/test_media_worker.py`,
   `tests/test_opengraph.py`.
5. Replace `except (Error, Exception)` catch-alls inside moved handlers with
   the concrete exception types already handled around them, keeping the
   broad catch only at the queue boundary in `run_job`.

### Files

- `src/micronote/handlers.py` (new)
- `src/micronote/worker.py`
- `tests/test_cache_object.py`, `tests/test_announce_handling.py`,
  `tests/test_media_worker.py`, `tests/test_opengraph.py`

### Verification

- `pytest`, `make lint`.
- Eager smoke and a real worker run
  (`docker-compose -f docker-compose-dev.yml up -d`) draining a follow and a
  create job.

### Exit Criteria

- `worker.py` is roughly ≤ 250 lines and contains no domain logic.
- Job handlers are importable and testable without the queue loop.

### Dependencies

Phase 1 (`jobs.py`). If skipped, keep status constants where they are and
move only the handlers.

---

## Phase 8 — Deferred Key Material & Version

### Why

- Importing `micronote.config` runs a VCS subprocess (`config.py:46-62`),
  generates key files (`config.py:201-211` via `utils/key.py`), and builds
  the RSA key, JWT serializer, admin API key, and actor document
  (`config.py:201-236`).
- Tests and scripts that only need constants still pay for (and can fail
  from) those side effects.

### Steps

1. Convert `config.py` service construction to cached accessors:
   `version()`, `key()`, `me()`, `jwt()`, `admin_api_key()`,
   `flask_secret_key()`, and `user_agent()`. Keep pure settings constants as
   module-level values. Implement Python module-level `__getattr__` in `config.py`
   to lazily evaluate `ME`, `KEY`, and `VERSION` on first attribute access. This
   guarantees backward compatibility with Jinja templates (`config.ME.url`,
   `config.ME.icon.url`) and existing callers without eager import-time execution.
2. Update call sites:
   - `app.py`: Flask secret key (`app.py:54`), context processor, and
     `activity_json()` content type.
   - `api.py`: `ADMIN_API_KEY`, `JWT.loads`.
   - `indieauth.py`: JWT issue and verify.
   - `activitypub.py`: `KEY`, `ME`, user agent.
   - `worker.py`: `KEY`, `USER_AGENT`.
   - `instance.py`: build `MY_PERSON` from `me()`.
   - Tests: `tests/test_authorized_fetch.py`, `tests/test_reply_handling.py`,
     `tests/test_activitypub_delete.py`.
3. Verify that importing `micronote.config` alone performs no file writes
   and no subprocess calls.

### Files

- `src/micronote/config.py`, `instance.py`, `app.py`, `api.py`,
  `indieauth.py`, `activitypub.py`, `worker.py`
- `tests/test_authorized_fetch.py`, `tests/test_reply_handling.py`,
  `tests/test_activitypub_delete.py`

### Verification

- `pytest`, `make lint`.
- `MICRONOTE_CONFIG_DIR=$(mktemp -d) python -c "import micronote.config"` and
  confirm the temp directory stays empty.
- Eager smoke and worker boot with a fresh config directory.

### Exit Criteria

- Importing `config` has no writes and no subprocesses.
- Service accessors are cached and covered by the existing tests.

### Dependencies

Phase 1 (import graph). Independent of Phases 2–7.

---

## Backlog (Not Planned Yet)

These are real findings that were deliberately deferred because they are
lower value, higher risk, or blocked by the phases above:

- **Services module** — replace `DB` / `MEDIA_CACHE` / `instance.py` module
  globals with cached `db()`, `media_cache()`, `backend()`, `person()`
  accessors (roughly 150 call sites). Requires Phase 8 first.
- **Auth consolidation** — merge password + WebAuthn login (`admin.py:304`),
  WebAuthn registration (`app.py:324`), IndieAuth, and session helpers into
  one `auth` package with an explicit interface.
- **Backend callback split** — `MicroblogPubBackend` mixes persistence with
  network fetches (`activitypub.py:587-651`) and counter updates; extract
  domain modules the callbacks delegate to.
- **Exception taxonomy** — remaining `except (Error, Exception)` sites
  outside Phase 7's handlers should catch concrete types where the behavior
  is provably unchanged.
- **Admin debug parameter** — `debug_inbox` (`admin.py:287-288`) drops all
  stream filters when set; gate it behind `DEBUG_MODE` like `/drop_cache`.

---

## Phase Status

| Phase | Status | Topic / Bookmark | Landed |
| --- | --- | --- | --- |
| 0 — Baseline & structural guardrails | Not started | `soc-phase-0-baseline` | — |
| 1 — Module boundaries & import cycles | Not started | `soc-phase-1-boundaries` | — |
| 2 — Cache ownership & invalidation | Not started | `soc-phase-2-cache` | — |
| 3 — Decompose `app.py` into blueprints | Not started | `soc-phase-3-blueprints` | — |
| 4 — HTML vs ActivityPub responses | Not started | `soc-phase-4-negotiation` | — |
| 5 — Data-access layer & helpers | Not started | `soc-phase-5-repository` | — |
| 6 — Media resolution out of filters | Not started | `soc-phase-6-media` | — |
| 7 — Worker queue runner vs handlers | Not started | `soc-phase-7-handlers` | — |
| 8 — Deferred key material & version | Not started | `soc-phase-8-config` | — |

Update this table when a phase starts or lands, and record the topic name
actually used so a later phase can find the history.
