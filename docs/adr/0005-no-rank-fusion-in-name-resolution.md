# ADR 0005 — No `$rankFusion` / `$scoreFusion` in name resolution or concentration

**Status:** accepted · **Date:** 2026-11-12

## Context

Hybrid search (`$rankFusion`, reciprocal rank fusion; `$scoreFusion`, normalised
score fusion) combines lexical and vector rankings over the **same collection**.
As of 2026-11-12 `$rankFusion` is documented without a Preview label from 8.0 (8.2
inside views) and `$scoreFusion` requires 8.3. The demo cluster runs 9.0, so either
operator is available. A portfolio review flagged their absence as a gap.

## What each panel actually ranks

- **Name resolution** (`backend/app/db/search.py`) ranks company names and
  partner names. A name is an identifier, not a meaning: "Construtora Alfa SA"
  and "Construtora Alpha S.A." must match because of spelling, and two different
  companies with semantically similar names must **not** match. Exact, fuzzy
  (`maxEdits: 2`) and autocomplete clauses in one `compound.should` already fuse
  those lexical signals inside one Lucene score. There are no name embeddings, and
  adding them would rank lookalike names of unrelated companies higher — the
  wrong behaviour for entity resolution.
- **Concentration** (`backend/app/db/concentration.py`) compares activity
  descriptions by meaning on `activities` (32 documents) with an **exact**
  vector search and a fixed threshold. There is no ranked list to fuse: every
  activity is scored and the threshold decides.
- Companies and people live in different collections; `$rankFusion` input
  pipelines run against one collection, so it cannot replace the Python merge
  that puts in-group results first.

## Decision

Do not add `$rankFusion` or `$scoreFusion`. The hybrid pattern this POV
demonstrates is the one that fits graph work: Atlas Search finds the entity, and
`$graphLookup` expands it in the same cluster.

## When to revisit

- A registry with free-text activity descriptions per company, where a lexical
  query ("obras") and a semantic one ("construção pesada") should rank the same
  companies: fuse `$search` on the description with `$vectorSearch` on its
  embedding, and measure it against labelled pairs before claiming a gain.
- A request to search investigation notes or KYC documents attached to companies.
