# Good first issues

The following GitHub issues are intentionally scoped for a first contribution.
Comment on an issue before starting, keep the change inside its stated scope,
and follow [CONTRIBUTING.md](../CONTRIBUTING.md) for tests and documentation.

## Ready to claim

- [#1 Add PowerShell completion generation](https://github.com/fly1d/repomin/issues/1)
  extends the dependency-free completion command and has a focused CLI test
  surface.
- [#2 Add a Chinese quick start](https://github.com/fly1d/repomin/issues/2)
  creates a concise, runnable entry point without translating the full reference
  manual.
- [#3 Support Directory.Build.props in the MSBuild adapter](https://github.com/fly1d/repomin/issues/3)
  reuses the existing hardened XML reducer and adds one network-free fixture.

## Proposing another starter task

A good starter issue should describe one user workflow, name the likely files,
define observable acceptance criteria, and avoid changing reduction semantics.
Suitable areas include documentation examples, completion ergonomics, strict
manifest extensions that reuse an existing parser, and deterministic benchmark
assertions. Open a feature request before implementing a new reducer or backend
whose trust boundary is not already documented.

Completed tasks are removed from this page so contributors do not begin stale
work. The complete project direction remains in [ROADMAP.md](ROADMAP.md).
