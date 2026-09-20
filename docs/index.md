---
type: index
title: Micronote.pub Documentation
description: Documentation index for the micronote.pub project, including migration history, architectural retrospectives, refactoring plans, and bug tracking.
status: stable
verified: machine-confirmed
stale_after: 2027-01-01T00:00:00Z
tags: [documentation, index]
timestamp: 2026-09-20T22:23:00+07:00
---

# Micronote.pub Documentation

Welcome to the technical knowledge base for `micronote.pub`.

---

## Sections

- [The Two Roads: Replatforming vs. Re-shaping a Microblog](./two-roads.md): Architectural essay and retrospective on diverging from upstream microblog.pub, honoring Thomas Sileo's craftsmanship, and the convergence on SQLite via NeoSQLite.
- [Defect Tracking & Bug Reports](./bugs/index.md): Standardized OKF defect reports for discovered security, logic, and performance bugs.
- [Separation of Concerns Improvement Plan](./improvement-plan.md): Phased, independently shippable refactoring plan for module boundaries, caching, presentation, and data access.
- [Modernization & Migration Plan](./migration.md): Engineering roadmap and architecture documentation covering the migration from MongoDB, RabbitMQ, and Celery to NeoSQLite, active-boxes, and watch streams.
- [BlobMoji Font Subsetting & Upgrade Runbook](./blobmoji-font-subsetting.md): Operational guide, technical background, and automation for slicing BlobMoji2 COLRv1 fonts into chunked WOFF2 web font subsets.
