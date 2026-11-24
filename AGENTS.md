# Project Instructions — City Air Quality Platform

## 1. Project Context & Design Sources

- This repository implements a single-developer undergraduate thesis project: a Python full-stack platform for city- and station-level air quality monitoring and analysis. The target is a reproducible local application, not a production-scale distributed service. Scope is ten modules (M01–M10) and three workflows: user observation, administrator collection and governance, and research analysis.
- Authoritative project documents:
  - `docs/design/城市空气质量监测数据分析平台_系统总体设计方案_V3.0.md` (V3.0): requirements, architecture, data semantics, decisions D01–D13, modules M01–M10, acceptance cases T01–T12.
  - `docs/design/总体实施方案.md` (plan): implementation stages S01–S09, dependencies, stage deliverables, acceptance boundaries.
- Prior prototype and research evidence must not be treated as implementation or verification evidence for this formal repository.
- For formal frontend work, including tasks started at the repository root, read [frontend/AGENTS.md](frontend/AGENTS.md) and [frontend/DESIGN.md](frontend/DESIGN.md).
- Start with task-relevant sections using the navigation below. Expand to other sections or the complete documents when the task requires broader context:

| Task area | Read first |
|---|---|
| Data products and which one a query reads | V3.0 §6.1 |
| Source rights and admission gates | V3.0 §6.3, §12.4; plan §9 |
| Collection windows, retry, recovery | V3.0 §6.5; plan S03 |
| Observation identity, revisions, invalidation | V3.0 §7.2, §8.6; plan §6 (T06) |
| Time, units, AQI standards | V3.0 §8.1–8.2 |
| Coverage and statistics (E/O/V/E-common/P) | V3.0 §8.4–8.5; plan §6 (T04) |
| Query, auth, export contracts | V3.0 §9; plan §7 |
| Performance budget | V3.0 §10.2; plan §8 |
| Acceptance cases and completion states | V3.0 §13; plan §10 |

- Identifier convention: single letters L, C, S, T, D, H, F are data products (§3). S01–S09 are stages, T01–T12 acceptance cases, D01–D13 design decisions, R1–R6 risks.

## 2. Architecture & Responsibilities

- Target stack (V3.0 §5.2): Python 3.13 with uv; a Django 5.2 LTS modular monolith with Django REST Framework, Auth, ORM, migrations, and Admin; PostgreSQL as the authoritative business database. Project manifests and lockfiles record the actual pinned dependency versions; the approved architecture remains defined by V3.0 §5.2.
- Web, Celery Worker, and a single Celery Beat run as separate processes of one application, sharing domain services, configuration, and migrations. Redis only delivers tasks; it is never authoritative for observations, permissions, revision state, or task completion.
- Keep domain rules reusable across HTTP endpoints, background tasks, historical imports, and administrative operations. Centralize authorization, source-use checks, normalization, quality validation, revision handling, and query semantics in domain services; entry points must not each implement their own observation handling.
- Use HTTPX for per-product collection adapters; formal collection does not depend on a resident browser. User requests never trigger external collection: browsers read stored, authorized data through same-origin REST/JSON APIs.
- Prefer PostgreSQL/SQL for filtering, alignment, aggregation, and pagination. Use Pandas/NumPy only for bounded analytical subsets. Add derived storage or partitioning only when representative measurements justify it.
- Django Admin manages accounts and low-risk reference data only. High-impact collection, quality, source-rights, and revision operations go through controlled domain services.
- Formal frontend (stage S08, after S07): Vue 3, TypeScript, Vite, Vue Router, Pinia, Axios, Element Plus, ECharts. The frontend consumes backend-defined data semantics and does not recompute statistics or rights decisions.
- Outside the approved initial scope: microservices, Kafka, Elasticsearch, TimescaleDB, Kubernetes, prediction, AI reporting, and additional persistent data platforms.

## 3. Critical Domain Invariants

- Data products stay distinct: L (national city list), C (direct city observations), S (station observations), T (recent 24-hour city AQI trend), D (separately verified official daily report), H (licensed historical station data), F (synthetic fixtures). Never silently merge or substitute one product for another. Same-hour AQI values from L, C, and T remain distinct; when presented together, identify and reconcile them without overwriting or double-counting. The city's seven indicators (AQI and six pollutant concentrations) come from C. Station comparisons require compatible same-city S-to-S records, or confirmed same-city H-to-H records within one authorized historical dataset.
- Nationwide rankings use L values from one genuinely shared observation moment; L's pollutant `*Level` fields are grades, not concentrations. Official city AQI is never derived from station averages, and hourly AQI averages are never presented as official daily reports.
- Logical observation identity covers source product, entity or station-site epoch, reporting window, observation time or report date, and applicable standard series. City and station observations are stored separately. Station names or present-day locations alone do not establish historical identity, and same-name stations from different sources are not auto-merged.
- Keep observation, fetch, and execution times separate, with source time zone and original time semantics. Keep units and statistical windows explicit (for example CO mg/m³ vs µg/m³; hourly vs rolling 8-hour O₃). Determine the AQI standard version from source-stated or document-inferable evidence (V3.0 §8.1–8.2), never by collection date alone. When the version is unknown, preserve available source-provided descriptions; do not infer version-specific AQI grades or colors, or mix such values into rankings that require a confirmed standard version. Missing, censored (such as `<3`), invalid, and genuine zero values remain distinguishable.
- Repeated ingestion must not create duplicate observations. A trusted newer source version advances the version watermark even when content is unchanged. A late older version never rolls back confirmed state, and unordered conflicting values stay disputed rather than becoming authoritative (T06).
- Collection reconciliation uses PostgreSQL transactions and business-window uniqueness. Scheduled, manual, and retried attempts must not create independent completion facts for one logical window. HTTP success alone does not prove new observations or complete coverage.
- Explicit historical windows are distinct from latest-valid queries. Never fill missing historical slots with newer observations. Selection semantics stay consistent across APIs, analysis, charts, tables, saved queries, and exports.
- Official observation values are never edited in place and anomalous peaks are never silently deleted. Corrections go through revision handling and quality events, including for administrators.
- Compute statistics only from compatible observations. Preserve the E/O/V/E-common/P coverage semantics and the exploratory thresholds in V3.0 §8.4. Insufficient evidence must not produce whole-period rankings or official regulatory conclusions.
- Keep H and F provenance explicit and separate. Synthetic observations cannot serve as evidence of real city publication or successful online operation.

## 4. Data Rights & Operating Boundaries

- Evaluate source rights separately for acquisition, retention, display, analysis, export, redistribution, and backup. Rights are scoped by product, operation, audience, geography, validity, and applicable limits. Unverified uses stay disabled.
- Publicly accessible endpoints, open-source code licenses, and successful HTTP requests do not establish permission to collect or reuse observation data. Source code, observation data, and map assets have separate usage conditions.
- Without applicable retention rights, do not persist raw source responses, or logs, screenshots, or payloads from which they can be restored, in databases, files, fixtures, or backups; summaries and hashes need a use check as well. Repository inclusion, packaging, and redistribution require separate applicable rights. Hong Kong validation samples and CNEMC verification receipts must not be used as project seed data or distributable fixtures; use licensed H (with attribution) or F instead.
- Keep CNEMC scheduled collection and other unapproved source operations disabled until the applicable rights and operating gates are satisfied. Follow the independent limited-trial and formal-online acceptance requirements in V3.0 §12.4 and plan §9.
- Apply both application-user authorization and source-use authorization. Administrative privileges do not confer data redistribution rights. Exports additionally require authorization for the requested operation and data versions.
- Distinguish loss of display rights from loss or expiry of retention rights. Implement affected-output invalidation and the applicable disposition rules for controlled exports and backups; restored data is checked against current rights and withdrawal records. Rehearse disposition and restore only on isolated, disposable data.
- Do not bypass source access restrictions, throttling, or security controls. Missing authorization or unsupported historical retrieval must not be worked around by replaying current observations into historical slots.
- Real maps require licensed geographic assets, verified location evidence, and known coordinate systems. A list-only fallback does not count as completed map acceptance.

## 5. Delivery & Project Verification

- Follow the S01–S09 dependency and acceptance structure in plan §4–§5. Deliver working vertical slices organized around user or administrator behavior, not scaffolding for empty modules.
- S07 is the backend acceptance gate that precedes formal frontend work (S08). S09 judges local delivery, map acceptance, and authorized city-online operation separately.
- Do not conflate completion states (plan §10.2): backend-stable, locally integrated, and fully complete V3.0 system are different claims. A list does not replace a map, and H does not replace city data.
- For changed domain boundaries, select the relevant T01–T12 cases (V3.0 §13) and stage exit criteria. Transactions, task recovery, ownership, versioning, and data disposition need evidence from real local PostgreSQL, Redis, Worker, and sessions, not mocks alone. Label evidence type: H, F, authorized real-source, or injected failure.
- A working prototype or successful local demonstration does not prove T11 city-online acceptance.
