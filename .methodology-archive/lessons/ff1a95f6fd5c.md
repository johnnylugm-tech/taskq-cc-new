---
key: ff1a95f6fd5c
source: gate-block
phase: 3
dimension: 
fr_ids: FR-01
created_at: 2026-08-24
---

**Failure:** Gate 1 blocked [arch_constraint_unconfigured]: A declared architecture constraint has an executor nobody switched on
**Fix:** Each entry names a constraint from the SAB's `architecture_constraints`, the tool this framework ALREADY RUNS that can decide it, and the configuration that would let it. These are not constraints the framework cannot check — they are constraints it is running the checker for while the checker has been told to ignore them. Two shapes: an import-linter contract of the named type is missing from .importlinter / setup.cfg, or bandit test ids are listed under `skips`. Write the contract, or remove the ids from `skips`, then re-run the gate. Do NOT delete the constraint from the SAB to clear this — a constraint nothing can decide is recorded and never blocked, so deleting a declaration only makes the record less true. Do not lower any dimension score to express it either: the configuration, not the code, is what failed.
