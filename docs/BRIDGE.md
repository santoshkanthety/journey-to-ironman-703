# Bridge: TriPeak Local ↔ Journey to Ironman SaaS

Two products, one product vision, zero shared code.

| | TriPeak Local (private lab) | Journey to Ironman (SaaS) |
|---|---|---|
| Repo | github.com/santoshkanthety/journey-to-ironman-703 | github.com/santoshkanthety/ironman-ace |
| Runs | localhost (Postgres + FastAPI + single-file app) | journeytoironman.app (Lovable · TanStack Start · Supabase) |
| Users | Santosh only | Public, auth + Stripe |
| Purpose | Fast experiments on real data; personal race prep | Productized, multi-tenant, billable |

## Rules of the road

1. **Separate repos, separate deploys, independent rollout.** A feature ships
   where it's ready; neither repo blocks the other.
2. **Specs are shared, code is not.** `docs/METRICS_SPEC.md` is identical in
   both repos — every formula, threshold, zone, and validation rule. Any change
   updates both copies the same day. Implementations differ freely.
3. **Local is the incubator.** New analytics/features prove out on real data
   locally first, then promote to SaaS as a written feature spec (schema
   migration + server function + route sketch) — not a code port.
4. **SaaS-born features** (auth, billing, onboarding, Strava OAuth web flow)
   stay SaaS-only unless the lab needs them.
5. **Lovable owns the working branch.** No force pushes, no history rewrites,
   branch always in a working state (see AGENTS.md). Builder commits here are
   additive: docs, migrations, server functions, routes.

## Data model mapping

| Local (Postgres `ironman` schema) | SaaS (Supabase `public`, RLS by `user_id`) |
|---|---|
| athletes.id (int) | auth.users.id (uuid) + profiles |
| athlete_settings | user_plan / profiles settings columns |
| activities.athlete_id | activities.user_id (RLS: own rows) |
| daily_vitals / daily_load | same names, user_id keyed |
| weekly_plan / planned_workouts | same names, user_id keyed |
| athlete_profile (thresholds) | athlete_profile |
| — | strava_connections, subscriptions, sync_jobs, user_roles |
| Analytics views (v_*) | to build: Postgres views or computed server-side |

## Feature parity matrix

| Feature | Local | SaaS | Next |
|---|---|---|---|
| Strava sync | ✅ API script + daily launchd | ✅ OAuth + sync | — |
| CTL/ATL/TSB engine | ✅ | ⚠️ verify vs spec | audit dashboard math against METRICS_SPEC |
| 16-week plan + editor + DQ checks | ✅ | ⚠️ plan route exists | port validation rules (spec §plan checks) |
| Kickoff (race date anchor + targets) | ✅ | ❌ | promote next — spec §kickoff |
| Readiness (green/yellow/red) | ✅ | ❌ | needs vitals source on SaaS |
| Health page (RHR/HRV/sleep/VO₂ zones) | ✅ | ❌ | needs Apple Health ingestion |
| Apple Health import (dedupe vs Strava) | ✅ | ❌ | SaaS: file upload + edge function, spec §dedupe |
| Statistics slice & dice | ✅ | ❌ | promote after kickoff |
| Best efforts / records / profile | ✅ | ❌ | cheap win from activities data |
| Auth / billing / onboarding | n/a | ✅ | — |
| Databricks analytics | ✅ | n/a | local-only |

## Workflow with the builder (Claude)

- "**promote X to SaaS**" → builder writes into ironman-ace: Supabase migration
  + server function(s) + route/component implementation per spec, commits to
  the Lovable branch in a working state.
- "**back-port X**" → builder implements the SaaS-born idea in the local stack.
- Spec drift found in either codebase → fix code to match spec, or change the
  spec in both repos deliberately.
