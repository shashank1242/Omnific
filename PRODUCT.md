# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
All four audiences equally (confirmed 2026-11-24):
- **Students and learners** who want to see a physical situation happen and build intuition for it.
- **Educators** who build demonstrations to show a class or audience.
- **Engineers and researchers** who sanity-check a scenario and care about the numbers, assumptions and sources.
- **Creators and hobbyists** (makers, game and video creators) who want a believable physical scene to look at and share.

Skill levels range from first-time learners to domain experts, so nothing may assume physics or programming
knowledge, and nothing may hide the detail an expert needs.

## Product Purpose
Omnific turns a scenario described in plain words (optionally with a sketch, photo, voice note or reference
documents) into an evaluated, inspectable 3D simulation. Success is a person getting a simulation they believe,
understand, can change, and want to share.

## Positioning
Creative freedom and scientific honesty carry equal weight (confirmed). Anything can be described in everyday
language, and every result is researched with sources, solved by generated code run in a sandbox, checked against
physics by automated evaluators, and shows its fidelity, assumptions and citations. Neither a physics engine (no
plain-language world building) nor a chatbot (no checked solver, no honest fidelity label) offers both.

## Operating Context
- Flow: describe → answer a few clarifying questions → watch the build (research, model, solver, run, checks, 3D
  scene) → explore the result (3D viewer, charts, trust, evaluation, code) → accept or ask for changes → optionally
  publish to the public library.
- Builds take minutes, so watching progress is a real part of the experience.
- The 3D viewer is the centre of a finished simulation: play/scrub time, orbit, drag objects, reset.
- People bring their own documents (PDFs, notes) that the agents cite.

## Capabilities and Constraints
- Web app: Next.js + Tailwind (`apps/web`), Three.js viewer (react-three-fiber). Light and dark mode.
- Fidelity labels: analytic, validated numerical, heuristic. They must stay honest and visible.
- Library 3D models need visible attribution (CC-BY).
- No user accounts yet; the public library is opt-in per simulation.
- Undecided: logo, brand colours, voice. Only the name is fixed.

## Brand Commitments
- Name: **Omnific**. Nothing else is committed yet (confirmed).

## Evidence on Hand
- Bundled demos in `apps/web/public/demo/` (pendulum, double pendulum, pizza on Venus, projectile on Mars).
- No testimonials, customers, usage numbers or press exist; never fabricate them.

## Product Principles
1. Wonder and honesty together: make the simulation delightful and always show how trustworthy it is.
2. Plain words first, depth on demand: a learner never needs jargon; an expert can always reach the equations,
   sources and code.
3. The simulation is the hero: interface recedes around the 3D world and its data.
4. The person stays in control: everything can be inspected, changed, reset and shared.

## Accessibility & Inclusion
Audiences include students and classrooms: keyboard access, visible focus, readable contrast in both themes,
reduced-motion support, and phone-width layouts are required (existing behaviour to preserve).
