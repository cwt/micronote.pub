# micronote

<p align="center">A self-hosted, single-user, <a href="https://activitypub.rocks">ActivityPub</a> powered microblog.</p>

Forked from <a href="https://github.com/tsileo/microblog.pub">microblog.pub</a>, modernized: SQLite storage via <a href="https://github.com/cwt/neosqlite">NeoSQLite</a>, federation via <a href="https://github.com/cwt/active-boxes">active-boxes</a>, no task broker, plain CSS.

## Features

 - Implements a basic [ActivityPub](https://activitypub.rocks/) server (with federation)
   - Compatible with [Mastodon](https://github.com/tootsuite/mastodon) and others (Pleroma, Hubzilla...)
   - Also implements a remote follow compatible with Mastodon instances
 - Exposes your outbox as a basic microblog
 - Implements [IndieAuth](https://indieauth.spec.indieweb.org/) endpoints (authorization and token endpoint)
   - You can use your ActivityPub identity to login to other websites/app
 - Comes with an admin UI with notifications and the stream of people you follow
 - Allows you to attach files to your notes
   - Privacy-aware image upload endpoint that strip EXIF meta data before storing the file
 - No JavaScript, **that's it**. Even the admin UI is pure HTML/CSS
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
   - Local ActivityPub matrix harness (see `docs/migration.md`) covers all supported actions without the fediverse
   - CI runs "federation" tests against two instances

## ActivityPub

micronote implements an [ActivityPub](http://activitypub.rocks/) server, it implements both the client to server API and the federated server to server API.

Activities are verified using HTTP Signatures or by fetching the content on the remote server directly.

## Running your instance

### Installation

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

### Deployment

```shell
$ docker-compose up -d
```

This starts the web app and the background worker sharing one SQLite file under `./data`.

## Development

The most convenient way to hack on micronote is to run the server locally, and the worker alongside it:


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

You can pass the `content` and `reply` via JSON, form data or query argument.

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
