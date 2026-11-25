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

The scope is modules M01-M10 and acceptance cases T01-T12. The formal frontend consumes server-defined authorization, products, query semantics, versions, coverage, and statistics. PostgreSQL remains authoritative; Redis transports tasks only. S07 is the backend baseline. S08 does not authorize real-source collection, broaden data rights, deploy, or pass S09/T11 acceptance.

## Evidence on Hand

V3.0, the overall implementation plan, the approved S08 implementation plan, `frontend/DESIGN.md`, the frozen v1 examples/OpenAPI, and S07 verification/handoff materials define product behavior. The prototype under `prototypes/frontend-preview/` and its F fixtures are references and counterexamples, not formal implementation or integration evidence. At S08 start, `frontend/` contains its engineering and design specifications but no runnable app.

## Product Principles

- Keep L, C, S, T, D, H, and F distinct and explicit.
- Let the backend decide authorization, source use, identity, version, and statistical meaning.
- Preserve observation and provenance; corrections use audited revision workflows.
- Treat insufficient evidence and unavailable authorization as explicit states, not inferred success.

## Accessibility & Inclusion

The frontend design specification adopts applicable WCAG 2.2 AA checks, including keyboard operation, visible focus, responsive layouts, readable chart alternatives, and reduced-motion behavior.
