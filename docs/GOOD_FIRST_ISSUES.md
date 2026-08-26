# Good first issues

The following GitHub issues are intentionally scoped for a first contribution.
Comment on an issue before starting, keep the change inside its stated scope,
and follow [CONTRIBUTING.md](../CONTRIBUTING.md) for tests and documentation.

## Ready to claim

- [Add a runnable Go module reduction example](https://github.com/fly1d/repomin/issues/7)
  - Document the existing `benchmarks/go-module` fixture in
    `docs/EXAMPLES.md`, including its oracle contract and expected payload.

Check the repository's [open issues](https://github.com/fly1d/repomin/issues)
for the latest scoped work, or propose a new starter task using the guidelines
below.

## Proposing another starter task

A good starter issue should describe one user workflow, name the likely files,
define observable acceptance criteria, and avoid changing reduction semantics.
Suitable areas include documentation examples, completion ergonomics, strict
manifest extensions that reuse an existing parser, and deterministic benchmark
assertions. Open a feature request before implementing a new reducer or backend
whose trust boundary is not already documented.

Completed tasks are removed from this page so contributors do not begin stale
work. The complete project direction remains in [ROADMAP.md](ROADMAP.md).
