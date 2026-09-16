---
type: index
title: Micronote.pub Defect Tracking Index
description: Registry and directory index of identified defects, vulnerabilities, and performance bottlenecks.
status: stable
verified: machine-confirmed
stale_after: 2027-01-01T00:00:00Z
tags: [bugs, tracking, index, quality-assurance]
timestamp: 2026-09-16T12:52:00Z
---

# Defect Tracking Registry

This index catalogs verified software defects, security vulnerabilities, and algorithmic bottlenecks identified during the deep code audit of `micronote.pub`.

---

## Active Defect Reports

| Bug ID | Title | Severity | Component | Status |
| --- | --- | --- | --- | --- |
| [BUG-001](./001.md) | String Splitting in IndieAuth Scopes Corrupts Authorization Tokens | High | IndieAuth | Resolved |
| [BUG-002](./002.md) | Unhandled TypeError and HTTP 500 Crash During WebAuthn Login | High | Admin / Auth | Resolved |
| [BUG-003](./003.md) | Typo in Database Field Name meta.exta in outbox_delete | Medium | ActivityPub Backend | Resolved |
| [BUG-004](./004.md) | Open Redirect Vulnerability via Protocol-Relative URL Bypass in safe_next_url | High | Session / Security | Resolved |
| [BUG-005](./005.md) | Non-Deterministic DOM Element IDs and In-Page Anchors via Python hash() | Medium | Template Filters | Resolved |
| [BUG-006](./006.md) | Follower Deduplication Script Crashes on Embedded ActivityPub Dict Objects | Low | CLI Maintenance | Open |
| [BUG-007](./007.md) | In-Place Query Mutation and Corrupted totalItems Count in build_ordered_collection | Medium | ActivityPub Pagination | Open |
| [BUG-008](./008.md) | Background Worker Fails Permanently on Invalid Remote Actor Icons | High | Background Worker | Open |
| [BUG-009](./009.md) | Unescaped Base URL in Notification Database Regular Expressions | Low | Admin Notifications | Open |
| [BUG-010](./010.md) | Severe Blocking N+1 Remote HTTP Calls in Inbox Stream Feed Endpoint | Critical | Stream API / Network | Open |

---

## Severity Distribution

- **Critical:** 1 ([BUG-010](./010.md))
- **High:** 4 ([BUG-001](./001.md), [BUG-002](./002.md), [BUG-004](./004.md), [BUG-008](./008.md))
- **Medium:** 3 ([BUG-003](./003.md), [BUG-005](./005.md), [BUG-007](./007.md))
- **Low:** 2 ([BUG-006](./006.md), [BUG-009](./009.md))
