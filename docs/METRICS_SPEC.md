# TriPeak Metrics Specification (canonical)

Single source of truth for every formula and threshold, shared verbatim by:
- **TriPeak Local** (private lab): github.com/santoshkanthety/journey-to-ironman-703
- **Journey to Ironman** (public SaaS): github.com/santoshkanthety/ironman-ace → journeytoironman.app

Change policy: any edit lands in BOTH repos the same day. Implementations may
differ (Python/Postgres vs TypeScript/Supabase); numbers may not.

## Training load

| Metric | Formula |
|---|---|
| IF (bike) | `norm_power / ftp_watts` |
| IF (run) | `threshold_pace_s_km / actual_pace_s_km` |
| IF (swim) | `css_pace_s_100m / actual_pace_s_100m` |
| IF (HR fallback) | `avg_hr / lthr_sport` |
| IF (no data fallback) | flat per sport: bike 0.68 · run 0.71 · swim 0.75 · strength 0.55 · other 0.55 |
| IF caps | power ≤1.3 · pace ≤1.2 (run) / ≤1.15 (swim) · HR ≤1.15 |
| TSS (bike) | `hours × IF² × 100` |
| TSS (run) | `hours × IF² × 100` |
| TSS (swim) | `hours × IF³ × 100` |
| TSS (no data) | `hours × IF_fallback² × 100` (³ for swim) |
| CTL | EMA of daily TSS, k = `1 − e^(−1/42)` |
| ATL | EMA of daily TSS, k = `1 − e^(−1/7)` |
| TSB | `yesterday's CTL − yesterday's ATL` |
| EF (bike) | `norm_power / avg_hr`, steady sessions ≥40min, IF < 0.85 |
| EF (run) | `(60000 / pace_s_km) / avg_hr` (m/min per bpm), same session filter |
| ACWR | `run_mi_7d / (run_mi_28d / 4)` |

## Zones & status (green ● / yellow ▲ / red ■ — always shape + color)

| Signal | Good ● | Watch ▲ | Risk ■ |
|---|---|---|---|
| Sleep h/night | ≥ 7 | 6–7 | < 6 |
| VO₂max (40s age) | ≥ 42 | 35–42 | < 35 |
| Resting HR | ≤ personal p40 | p40–p80 | > p80 |
| HRV (SDNN) | ≥ personal p60 | p25–p60 | < p25 |
| ACWR | 0.8–1.3 | 1.3–1.5 | > 1.5 |
| TSB (build) | −10..−25 | −25..−30 | < −30 |

Personal percentiles compute over the athlete's full history of monthly
averages and recalibrate as data grows.

## Readiness (daily)

RED if any: TSB < −30 · RHR ≥ baseline+7 · HRV < 0.85×baseline · sleep < 5h
YELLOW if any: TSB < −20 · RHR ≥ baseline+4 · HRV < 0.93×baseline · sleep < 6.5h
else GREEN. Baselines = latest athlete_profile row.

## Plan data-quality checks

Hard errors (block write): weeks ≠ exactly 1..16 unique · phase ∉
{base,build,peak,taper,race} · non-numeric · hours ∉ [0.5,25] · TSS ∉ [50,1200]
· swim_m ∉ [0,30000] · bike ∉ [0,310mi] · run ∉ [0,75mi] · sessions ∉ [0,14]
· long ride ∉ [0,480min] · long run ∉ [0,240min].
Warnings (write + surface): hours ramp > ramp_limit_pct (default 30%) between
consecutive non-recovery weeks · recovery week hours ≥ prior week · long run >
long_run_cap_min (default 150) · swim = 0 in any non-race week · race-week
hours > 8.

## Kickoff

`race_date` anchors the plan: `week_start(w) = monday(race_date) − (16−w)×7d`.
Race-week (16) contains race day. Clearing race_date nulls all start dates.
Validation: past date = error; < 16 weeks out = compressed warning; not Sunday
= warning; > 1 year = warning.

## Per-athlete settings (defaults)

ctl_target 65 · compliance_goal_pct 85 · ramp_limit_pct 30 ·
long_run_cap_min 150 · tsb_race band +5..+15 · units imperial.

## Units policy

Storage metric (meters, seconds, kg). Display imperial: miles, /mi pace,
feet elevation, lb. Swim volumes display meters (triathlon convention).

## Dedupe rules

Activities unique on `(athlete, source, external_id)`. Apple Health workouts
skipped when a Strava activity of the same sport exists within ±10min.
Apple export timestamp quirk: value is UTC, offset label is local — true local
= naive value + offset. Strava `start_date_local` = local wall-clock.

## Best efforts (proxy)

Fastest whole-run average pace among runs ≥ 0.98 × band distance, bands
5k/10k/15k/half/30k/marathon; plausible-pace guard 195–720 s/km.
