# Roadmap

ReproMin is a pre-alpha project. This roadmap describes the order of work,
not a promise of dates.

## Current: trustworthy reductions

- Keep the oracle contract explicit and fail closed on infrastructure errors.
- Maintain reproducible persistent sessions, holdout certification, and
  cross-platform execution.
- Expand offline benchmarks before adding a new reducer or backend.
- Keep reports, checkpoints, and documentation auditable for issue reports.

## Next: contributor scale

- Improve common CLI diagnostics for misconfigured paths, limits, and failure
  criteria.
- Publish coverage artifacts and benchmark trend summaries for pull requests.
- Add one carefully scoped ecosystem adapter with a network-free fixture.
- Improve examples for Java, Python, and semantic reduction workflows.

## Later: integrations

- Provide stable extension interfaces for remote workers and CI services.
- Add optional report exporters without changing the core reduction contract.
- Evaluate additional language analyzers only when they preserve hashed,
  parser-backed edits and deterministic rediscovery.

## Explicit non-goals

- Replacing a build system, test runner, or dependency resolver.
- Claiming code correctness from a passing oracle or holdout result.
- Running untrusted commands as a security sandbox on the host backend.
- Adding network-dependent tests to the offline benchmark suite.

Roadmap changes should be discussed in an issue or pull request and should
include the user workflow, oracle contract, test fixture, and documentation
impact.
