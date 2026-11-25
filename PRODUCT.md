# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

The approved local frontend stack is Vue 3, TypeScript, Vite, Vue Router, Pinia, Axios, Element Plus, and ECharts. The backend is the existing Django modular monolith with PostgreSQL as its authoritative business database. These choices come from the approved project design and S08 plan.

## Product Purpose

The platform supports local observation, research analysis, and data-governance workflows for city and monitoring-station air quality. Success means users can inspect authorized stored observations, revisit their own research work, and let authorized administrators trace collection and quality records without conflating data products or permissions.

## Positioning

This is a reproducible, single-developer thesis application, not a production-scale distributed service. Product meaning comes from explicit observation identity, provenance, time windows, quality, coverage, and source-use boundaries defined by V3.0.

## Operating Context

The three workflows are user observation, research analysis, and administrator collection/governance. The app is developed and evaluated locally with same-origin Django Session and CSRF authentication. Software-chain verification uses authorized H or clearly identified project-owned F data; F is never evidence of real city publication or online operation.

## Capabilities and Constraints

The scope is modules M01-M10 and acceptance cases T01-T12. A formal frontend consumes server-defined authorization, products, query semantics, versions, coverage, and statistics. PostgreSQL is authoritative. Redis transports tasks. The current backend baseline includes the S08 enhancements and the S07 frozen operations. T11 remains NO-GO.

## Evidence on Hand

V3.0, the overall implementation plan, `frontend/DESIGN.md`, the current v1 examples/OpenAPI, and the S07/S08 backend handoff describe the applicable behavior. The original S08 application and its integration evidence are archived at `b12f33f717ce5f1ccb18407b4e56ff9849aa4024`. `frontend/` contains the engineering and design specifications. `prototypes/frontend-preview/` runs the approved national overview with F fixtures. A new formal frontend has yet to be implemented. Final S08 integration and acceptance remain pending, and S09 has yet to start.

## Product Principles

- Keep L, C, S, T, D, H, and F distinct and explicit.
- Let the backend decide authorization, source use, identity, version, and statistical meaning.
- Preserve observation and provenance; corrections use audited revision workflows.
- Treat insufficient evidence and unavailable authorization as explicit states, not inferred success.

## Accessibility & Inclusion

The frontend design specification adopts applicable WCAG 2.2 AA checks, including keyboard operation, visible focus, responsive layouts, readable chart alternatives, and reduced-motion behavior.
