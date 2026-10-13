# Representative handoff fixtures

The evaluation matrix uses only synthetic, Git-tracked handoff inputs and
materializes every project under a temporary output root.

- F1 derives from `minimal/`: no prototype plan, no executable task, and a
  structured blocking planning gap.
- F2 derives a nonphysical multi-task variant from `task-matrix/` by changing
  the declared synthetic task effects and rebuilding the bundle manifest.
- F3 uses `task-matrix/` with its declared physical task and remains blocked
  until external validation is represented by an explicit evidence record.
- F4 derives a revision-2 bundle from `task-matrix/`, preserving the prior
  observation/result history while changing a mandatory requirement.
- F5 and F6 are terminal lifecycle variants generated from the same synthetic
  inputs: `COMPLETE_WITH_GAPS` and human-authorized `CANCELLED`.

F2 and F4 are intentionally transformed copies created inside the evaluation
temporary root, so no generated project or machine-specific path is stored in
this repository. See `tools/lib/evaluation.py` and
`tests/test_evaluation.py` for the executable assertions.
