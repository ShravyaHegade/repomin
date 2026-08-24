# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Opt-in `--semantic-reducer http` seam with a provider-agnostic
  OpenAI-compatible backend, default-off and with no third-party runtime
  dependency.
- Opt-in `--text-file RELATIVE_PATH` reducer for line-level shrinking of
  explicitly selected UTF-8 text files.
- Repeatable input controls: recursive gitignore, exact `--keep`, exact
  `--exit-code`, `--max-attempts`, and `--max-duration`.
- `benchmarks/run_offline.py` regression runner and an `offline-benchmarks` CI
  job.
- `input-controls`, `semantic-stub`, and `text-lines` benchmark fixtures.

### Changed

- Updated the Maven benchmark acceptance envelope for
  `hierarchical-fixed-point-v2`.
- Recorded `text_files` in reports and persistent-session identity.
- Stabilized Windows process, text, Java classpath, and test-fixture behavior;
  Windows is now a required CI matrix lane.

## [0.1.0.dev1] - 2026-08-24

Cross-platform pre-alpha feasibility build with semantic and text reducers,
persistent sessions, holdout certification, offline benchmarks, and contributor
documentation.

## [0.1.0.dev0] - Unreleased

Initial pre-alpha feasibility build.
