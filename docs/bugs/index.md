---
type: index
title: Micronote.pub Defect Tracking Index
description: Registry and directory index of identified defects, vulnerabilities, and performance bottlenecks.
status: stable
verified: machine-confirmed
stale_after: 2027-01-01T00:00:00Z
tags: [bugs, tracking, index, quality-assurance]
timestamp: 2026-09-20T00:00:00Z
---

# Defect Tracking Registry

This index catalogs verified software defects, security vulnerabilities, and algorithmic bottlenecks identified during the deep code audit of `micronote.pub`.

---

## Resolved Defect Reports

| Bug ID | Title | Severity | Component | Status |
| --- | --- | --- | --- | --- |
| [BUG-001](./001.md) | String Splitting in IndieAuth Scopes Corrupts Authorization Tokens | High | IndieAuth | Resolved |
| [BUG-002](./002.md) | Unhandled TypeError and HTTP 500 Crash During WebAuthn Login | High | Admin / Auth | Resolved |
| [BUG-003](./003.md) | Typo in Database Field Name meta.exta in outbox_delete | Medium | ActivityPub Backend | Resolved |
| [BUG-004](./004.md) | Open Redirect Vulnerability via Protocol-Relative URL Bypass in safe_next_url | High | Session / Security | Resolved |
| [BUG-005](./005.md) | Non-Deterministic DOM Element IDs and In-Page Anchors via Python hash() | Medium | Template Filters | Resolved |
| [BUG-006](./006.md) | Follower Deduplication Script Crashes on Embedded ActivityPub Dict Objects | Low | CLI Maintenance | Resolved |
| [BUG-007](./007.md) | In-Place Query Mutation and Corrupted totalItems Count in build_ordered_collection | Medium | ActivityPub Pagination | Resolved |
| [BUG-008](./008.md) | Background Worker Fails Permanently on Invalid Remote Actor Icons | High | Background Worker | Resolved |
| [BUG-009](./009.md) | Unescaped Base URL in Notification Database Regular Expressions | Low | Admin Notifications | Resolved |
| [BUG-010](./010.md) | Severe Blocking N+1 Remote HTTP Calls in Inbox Stream Feed Endpoint | Critical | Stream API / Network | Resolved |
| [BUG-011](./011.md) | Unhandled ActivityUnavailableError in _handle_replies Crashes Worker on Remote 401/503 Replies | High | ActivityPub / Worker | Resolved |
| [BUG-012](./012.md) | Unhandled ActivityUnavailableError in worker cache_object and cache_actor Crashes on Remote 401 Unauthorized | High | Worker / Federation | Resolved |
| [BUG-013](./013.md) | Unhandled ActivityUnavailableError in inbox_announce and Excessive Worker Retries on Remote 401 Actor Fetch | High | ActivityPub / Worker | Resolved |
| [BUG-014](./014.md) | Infinite Recursion in _build_thread When inReplyTo Self-References | High | Thread Builder | Resolved |

## Active Defect Reports

| Bug ID | Title | Severity | Component | Status |
| --- | --- | --- | --- | --- |
| [BUG-015](./015.md) | outbox_delete Calls get_object_sync() Multiple Times Redundantly | Low | ActivityPub Backend | Open |
| [BUG-016](./016.md) | Duplicate post_to_outbox Logic in tasks.py and activitypub.py | Low | Outbox Post / Dead Code | Open |
| [BUG-017](./017.md) | serve_grid_file Content-Type Fallback Allows MIME Confusion on /uploads Endpoint | Medium | Media Serving | Open |
| [BUG-018](./018.md) | _GRIDFS_CACHE Is Unbounded and Grows Without Eviction in Gunicorn Workers | Medium | Template Filters / Memory | Open |
| [BUG-019](./019.md) | _PENDING_CACHE_JOBS DB Dedup Query Is Kind-Blind, Silently Drops Jobs | Low | Media Cache / Worker | Open |
| [BUG-020](./020.md) | inject_config Runs 5 Separate count_documents Queries on Every Template Render | Low | Context Processor / Performance | Open |
| [BUG-021](./021.md) | /drop_cache Route Has No DEBUG_MODE Guard and Can Wipe Production Caches | Medium | Admin / Security | Open |
| [BUG-022](./022.md) | admin.html Receives Unused 'instances' Variable from Empty Collection Query | Low | Admin / Dead Code | Open |
| [BUG-023](./023.md) | Pin/Unpin API Does Not Invalidate Page Cache, Serving Stale Homepage for 12h | Medium | Cache / API Consistency | Open |
| [BUG-024](./024.md) | MY_PERSON and back Instantiated Three Times; api.back Silently Overrides app.back | Low | Module Init / Duplication | Open |

---

## Severity Distribution

**Resolved:**
- **Critical:** 1 (BUG-010)
- **High:** 8 (BUG-001, BUG-002, BUG-004, BUG-008, BUG-011, BUG-012, BUG-013, BUG-014)
- **Medium:** 3 (BUG-003, BUG-005, BUG-007)
- **Low:** 2 (BUG-006, BUG-009)

**Open:**
- **High:** 0
- **Medium:** 4 (BUG-017, BUG-018, BUG-021, BUG-023)
- **Low:** 6 (BUG-015, BUG-016, BUG-019, BUG-020, BUG-022, BUG-024)
