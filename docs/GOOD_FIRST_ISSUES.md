# Good first issues

The following GitHub issues are intentionally scoped for a first contribution.
Comment on an issue before starting, keep the change inside its stated scope,
and follow [CONTRIBUTING.md](../CONTRIBUTING.md) for tests and documentation.

## Ready to claim

### [#5 Complete shell support for report validation commands](https://github.com/fly1d/repomin/issues/5)

Extend the existing dependency-free Bash, Zsh, Fish, and PowerShell completion
scripts so contributors can discover `repomin report validate`, its report and
payload paths, and `--json`. This is scoped to completion generation and focused
tests; it does not change reduction or report-validation semantics.

### [#6 Document report validation in the Chinese quick start](https://github.com/fly1d/repomin/issues/6)

Add a concise, copy-pasteable report-validation workflow to the existing
Chinese quick start. The guide should cover payload fingerprint checking, JSON
output for scripts, exit behavior, and the boundary between structural
validation and rerunning or proving the reproduction.

## Proposing another starter task

A good starter issue should describe one user workflow, name the likely files,
define observable acceptance criteria, and avoid changing reduction semantics.
Suitable areas include documentation examples, completion ergonomics, strict
manifest extensions that reuse an existing parser, and deterministic benchmark
assertions. Open a feature request before implementing a new reducer or backend
whose trust boundary is not already documented.

Completed tasks are removed from this page so contributors do not begin stale
work. The complete project direction remains in [ROADMAP.md](ROADMAP.md).
