---
type: essay
title: "The Two Roads: Replatforming vs. Re-shaping a Microblog"
description: A retrospective on diverging from upstream microblog.pub, honoring Thomas Sileo's craftsmanship, and exploring how two different engineering philosophies arrived at the same peaceful SQLite destination.
status: stable
verified: human-authored
stale_after: 2027-09-20T00:00:00Z
tags: [architecture, retrospective, microblog, activitypub, sqlite, indieweb, neosqlite]
sources:
  - https://github.com/tsileo/microblog.pub
  - https://sr.ht/~tsileo/microblog.pub/
  - https://github.com/tsileo/little-boxes
  - https://github.com/cwt/neosqlite
  - https://github.com/cwt/active-boxes
  - ./migration.md
  - ./improvement-plan.md
timestamp: 2026-09-20T00:00:00Z
---

# The Two Roads: Replatforming vs. Re-shaping a Microblog

This is the story of this repository — and of a project I left resting on a shelf for over six years.
It is an appreciation of upstream `microblog.pub` and its creator, Thomas Sileo; a comparison of two
different engineering philosophies; and a personal account of how an ambitious systems project called
NeoSQLite quietly solved an architectural puzzle while nobody was looking.

## Where this repo started: The cabin in the woods

In 2018, the Fediverse was surging into public consciousness, largely propelled by Mastodon. But
running Mastodon was — and remains — an operational undertaking: PostgreSQL, Redis, Elasticsearch,
Sidekiq workers, Puma app servers, Node streaming daemons, and gigabytes of memory.

Amidst that infrastructure heavy-weather, Thomas Sileo offered something profoundly refreshing:
**`microblog.pub`**.

Thomas envisioned a tranquil, single-user, self-hosted microblog where you held complete sovereignty
over your words and identity. He anchored it in the IndieWeb ethos — IndieAuth for decentralized
login, Microformats for semantic markup, Micropub for authoring, Webmentions for conversation — and
treated visitor privacy with uncompromising respect: zero tracking, EXIF metadata stripped on
upload, proxied remote media, and zero client-side JavaScript required to read or post. To make it
federate with the wider ActivityPub universe, Thomas didn't wait for the ecosystem to mature; he
wrote **`little-boxes`**, an independent Python ActivityPub library that passed the official W3C
implementation test suite.

By the time I stumbled across `microblog.pub` in January 2019, Thomas had poured hundreds of commits
into shaping this vision. For me, discovering it felt like finding a cozy, hand-built cabin in the
woods. It was small enough to understand, open enough to hack on, and built with genuine care.

I moved in as an eager tinkerer. I was the person fascinated by the small, tactile details: fixing
emoji rendering glitches on Firefox, smoothing out responsive layout quirks, introducing the warm
Google Mali typography, adding installable PWA support, hooking up PeerTube embeds, generating
JSON/Atom/RSS feeds, crafting ARM64 Docker images, and styling the soft yellow notepad aesthetic
that eventually gave `micronote.pub` its name.

Between January 2019 and May 2020, I logged 91 commits (87 in 2019, 4 in early 2020). I split apart
some internal modules (`api`, `IndieAuth`, `feeds`, `admin`) to fit my own workflow and pinned a
fork of `little-boxes`. And then, as often happens in open source and in life, my attention was pulled
elsewhere. The server kept running, but the repository fell silent. For six years, I didn't touch a
single line of code.

## What upstream was doing: The artisan's courage

While my fork slept, Thomas Sileo and the `microblog.pub` community never stopped tending the garden.
Through mid-2020, community contributors enhanced the original codebase with favicons, `alsoKnownAs`
aliases, private question handling, actor-icon caching, and hardened session security.

Then, in June 2022, Thomas did something extraordinary that requires immense creative courage: he
stepped back, looked at the first-generation architecture, and chose to rebuild it cleanly from
first principles.

The original 2018 stack had been built with the conventional tools of its era — Flask, Tornado,
MongoDB, GridFS, RabbitMQ, and Celery. They were powerful tools, but for a single-user microblog,
running a document database daemon and an AMQP message broker was undeniably heavy. It added
operational friction for independent operators who just wanted a modest blog on a $5 VPS or a
home server.

In **June 2022**, Thomas launched the `v2` development effort, moving primary development to
[SourceHut](https://sr.ht/~tsileo/microblog.pub/) with a mirror on GitHub. By the autumn of 2022 —
right as the mainstream social web was fracturing and thousands of people were seeking decentralized
alternatives — Thomas delivered a masterclass in modern, focused software craft:

- **Web framework:** Moved from Flask/Tornado to native asynchronous **FastAPI** + **Uvicorn**.
- **Database:** Replaced MongoDB with **SQLite**, backed by **SQLAlchemy** (asyncio) and
  **Alembic** migrations, establishing a crisp, explicit relational schema across actors, inboxes,
  outboxes, followers, uploads, and notifications.
- **Task queue:** Swept away Celery and RabbitMQ entirely. In their place, he introduced lean,
  database-backed activity queue tables (`IncomingActivity` and `OutgoingActivity`), serviced by
  asynchronous worker loops (`app/utils/workers.py`) managed under `supervisord`.
- **ActivityPub layer:** Distilled the lessons of `little-boxes` into a unified, in-tree
  ActivityPub engine (`boxes.py`).
- **Storage:** Placed configuration, database, uploads, and keys into a single, cohesive `data/`
  directory that could be backed up with a simple `tar` command.

It was a tour de force of intentional pruning. Thomas stripped away accidental complexity to create a
pure, durable instrument for personal publishing.

## What happened in the meantime: An eviction and an accidental engine

My own path back to this codebase was entirely serendipitous. It didn't start with ActivityPub, and
it didn't start in this repository.

I did not build NeoSQLite for this microblog. I built it for fun — but in the obsessive, deep-in-the-engine-room
sense of the word that hackers know well. The project began with an ambitious intellectual bet:
*Can you take the world's most ubiquitous, rock-solid relational database — SQLite — and give it the
full developer ergonomics, query semantics, and schema flexibility of MongoDB, without ever running a
database daemon?*

It was never a casual wrapper around `json_extract()`. To achieve genuine PyMongo drop-in compatibility
and real performance required building a serious, full-fledged database system:

- **A Three-Tier Query Execution Engine:** Tier 1 translates MongoDB queries, projection operators,
  and complex aggregation pipelines directly into native SQLite Common Table Expressions (CTEs) and
  JSON expressions for a 10–100x speedup. Tier 2 engages SQLite FTS5 virtual tables for text search,
  hybrid scoring, and temporary table hash-joins. Tier 3 provides an authoritative in-memory Python
  pipeline fallback, ensuring 100% semantic fidelity down to MongoDB's subtlest edge cases.
- **Full ACID Transactions:** Complete `ClientSession` support mimicking PyMongo 4.x semantics,
  engineered on top of SQLite `SAVEPOINT`s and WAL mode.
- **Embedded GridFS:** Modern `GridFSBucket` and legacy `GridFS` APIs implemented natively,
  allowing binary assets and metadata chunks to live directly inside SQLite tables (`fs.files` and
  `fs.chunks`).
- **Trigger-Backed Change Streams (`watch()`):** Rather than requiring a multi-node MongoDB replica
  set, Kafka, or Redis, NeoSQLite engineered change streams using shared, reference-counted SQLite
  triggers logging to a bounded changelog table. The engine provides per-stream watermarks, library-side
  `$match` pipeline pushdown, and crash-resilient `resume_after` / `start_after` tokens that survive
  process restarts without missing or duplicating events.
- **Wire Protocol Emulation:** It even sprouted **NX-27017**, a standalone server implementing the
  binary MongoDB wire protocol so external tools, debuggers, and official MongoDB drivers could talk
  directly to an embedded SQLite database over port 27017.

Over 930 commits, 80 test suites, and strict differential verification against live MongoDB instances,
NeoSQLite matured into a battle-hardened embedded document database. It was a labor of pure systems
craftsmanship, pursued for the joy of solving hard problems without any commercial agenda.

Then, reality intervened.

For years, I had hosted a personal Mastodon instance on an Oracle Cloud free-tier VM. One afternoon,
without warning, Oracle reclaimed the VM. In a flash, the instance vanished: PostgreSQL, Redis,
Elasticsearch, Sidekiq queues, and years of configurations were wiped clean.

I faced the dilemma every self-hoster eventually encounters. Did I really want to spend another weekend
re-provisioning a multi-container cloud infrastructure — five separate daemons and hundreds of megabytes
of idle memory — just to post short notes into the Fediverse? Or was there a saner, quieter way to live
online?

That was when I remembered my 2019 notepad fork. But the prospect of resurrecting MongoDB, GridFS,
and RabbitMQ felt just as unappealing as rebuilding Mastodon. And then the realization hit:

*NeoSQLite had already solved every single piece of the puzzle.*

Because NeoSQLite implemented `GridFSBucket`, all media attachments could live natively inside the
SQLite database. Because NeoSQLite provided 12-byte BSON `ObjectId`s and PyMongo document semantics,
the first-generation ActivityPub data model required zero translation layers or schema migrations.
And most critically: because NeoSQLite implemented trigger-driven `watch()` with resume tokens, a
single lightweight worker process tailing a `jobs` collection could replace Celery, RabbitMQ, and
Redis in one stroke.

The entire server farm collapsed into a single `.db` file in WAL mode.

When I reopened this repository in **September 2026** after six years of silence, the modernization
was not an agonizing slog. The heavy machinery had already been forged over years of deep engineering on
NeoSQLite. The commit that cut the umbilical cord — *"Run on NeoSQLite, no MongoDB or RabbitMQ needed"* —
looked deceptively simple in the Mercurial log, but it was really the quiet culmination of two separate
journeys meeting at the exact right bend in the road.

## The two stacks, side by side

Seeing how Thomas rebuilt `microblog.pub` and how `micronote.pub` reshaped itself reveals two
different, deeply thoughtful answers to the exact same operational challenge:

| Concern | Upstream `microblog.pub` (v2) | This repo, `micronote.pub` |
| :--- | :--- | :--- |
| **Creator / Steward** | Thomas Sileo & community | Chaiwat Suttipongsakul (fork) |
| **Primary Forge** | SourceHut (`~tsileo/microblog.pub`) + GitHub mirror | Bashell / Mercurial + GitHub |
| **Database Engine** | SQLite via SQLAlchemy (asyncio) + Alembic | NeoSQLite (3-tier document engine: SQL CTE translation, FTS5, Python fallback) |
| **Data modeling** | Explicit relational schema (Foreign Keys) | JSON document store (ActivityPub native) |
| **Web framework** | FastAPI + Uvicorn (native async) | Flask + Gunicorn (synchronous WSGI) |
| **Concurrency model**| Async request handling throughout | Sync HTTP request loop, async federation engine |
| **Media storage** | Filesystem storage under `data/uploads/` + signed proxy | SQLite-embedded `GridFSBucket` (`fs.files` / `fs.chunks` in `data/micronote.db`) |
| **Background workers**| Async polling worker loops on SQLite tables (`Worker`) | Reactive `watch()` change-stream on `jobs` collection via SQLite triggers |
| **ActivityPub engine**| In-tree `boxes.py` | `active-boxes` (modernized fork of `little-boxes`) |
| **Asset pipeline** | SCSS compiled via Boussole | Plain CSS with native CSS custom properties |
| **Aesthetic identity**| Clean, minimalist, responsive typographic layout | Yellow notepad theme, Google Mali font, BlobMoji |
| **Data directory** | `data/` (SQLite DB + uploads directory + keys) | `data/` (single `micronote.db` file + keys) |

*(A brief note on tooling: Boussole in upstream `microblog.pub` is an SCSS compilation tool, while the
actual background activity dispatching is handled by custom async workers in `app/utils/workers.py`.)*

Where upstream intentionally streamlined its feature footprint to remain light and laser-focused,
`micronote.pub` retained the eclectic touches that had grown into its identity: the Google Mali
handwriting typeface, BlobMoji font subsets, PWA capabilities, rich PeerTube embeds, and
multi-format syndication feeds. One feature we did bid a fond farewell to was the Yandex
auto-translation layer, which was retired during the 2026 migration when the upstream API ceased
offering a free tier.

## Two roads to the same file

There is a quiet poetry in where these two paths led.

Thomas set out to solve **operational simplicity**. He looked at the 2018 stack, recognized that
multi-daemon architectures were suffocating the indie web, and boldly rebuilt the application around
the modern async Python ecosystem and SQLite.

I arrived at the same SQLite file seeking **continuity and conservation**. I wanted to preserve the
familiar documents, the tested behavior, and the personality of a codebase I loved, without paying the
tax of running database servers.

In software architecture, people often argue about **replatforming** versus **re-shaping**:

- **Replatforming** (Thomas's road) takes boldness. You discard obsolete patterns, welcome the latest
  idioms of your language, and build a clean foundation that invites fresh contributors to build with
  standard, modern tools. It is cleaner, leaner, and forward-looking.
- **Re-shaping** (this repo's road) is restorative craftsmanship. Like restoring a vintage roadster,
  you preserve the classic chassis, the hand-stitched leather, and the familiar dashboard dials, but
  you discreetly replace the heavy cast-iron engine underneath with a quiet, modern motor.

Both paths honor the true spirit of the open web.

`microblog.pub` and `micronote.pub` are cousins today. They share the same grandparents, the same
uncompromising respect for personal autonomy, and the exact same destination: a humble, single-file
database resting peacefully on a modest server that you own.

To Thomas Sileo: thank you for having the imagination to build `microblog.pub` in the first place,
for crafting `little-boxes` when few others were writing ActivityPub in Python, and for showing all of
us what principled, independent software looks like.

## References

- [Upstream `microblog.pub` on SourceHut](https://sr.ht/~tsileo/microblog.pub/)
- [Upstream `microblog.pub` on GitHub](https://github.com/tsileo/microblog.pub)
- [Official `microblog.pub` Documentation](https://docs.microblog.pub/)
- [Thomas Sileo's `little-boxes` on GitHub](https://github.com/tsileo/little-boxes)
- [NeoSQLite document database engine](https://github.com/cwt/neosqlite)
- [Active-boxes federation library](https://github.com/cwt/active-boxes)
- [Migration plan (this repo)](./migration.md)
- [Separation-of-concerns improvement plan (this repo)](./improvement-plan.md)
