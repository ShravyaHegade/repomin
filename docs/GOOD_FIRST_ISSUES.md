# Good first issues

The following GitHub issues are intentionally scoped for a first contribution.
Comment on an issue before starting, keep the change inside its stated scope,
and follow [CONTRIBUTING.md](../CONTRIBUTING.md) for tests and documentation.

## Ready to claim

These issues are intentionally small and have explicit acceptance criteria:

- [Add a runnable Java and Gradle reduction example](https://github.com/fly1d/repomin/issues/12)
  - documentation-first; demonstrates a complete structured reduction and its
    report validation boundary.
- [Show report validation as a CI gate](https://github.com/fly1d/repomin/issues/13)
  - documentation-first; demonstrates JSON validation, payload fingerprints,
    and the Action artifact contract.

The [real CI failure pilot](https://github.com/fly1d/repomin/issues/11) is also
open for users who have a sanitized workflow to share. Check the repository's
[open issues](https://github.com/fly1d/repomin/issues) for newly proposed work,
or use the template below to suggest a focused contribution.

Completed tasks are removed from this page so contributors do not begin stale
work.

## Proposing another starter task

A good starter issue should describe one user workflow, name the likely files,
define observable acceptance criteria, and avoid changing reduction semantics.
Suitable areas include documentation examples, completion ergonomics, strict
manifest extensions that reuse an existing parser, and deterministic benchmark
assertions. Open a feature request before implementing a new reducer or backend
whose trust boundary is not already documented.

The complete project direction remains in [ROADMAP.md](ROADMAP.md).
