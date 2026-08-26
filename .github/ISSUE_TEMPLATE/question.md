---
name: Usage question
about: Ask for help reproducing a failure or interpreting a ReproMin result
title: ""
labels: question
assignees: ""
---

## What are you trying to do?

Describe the repository, failing command, and reduction goal. Remove secrets,
proprietary source, and credentials before posting.

## Environment

- OS and architecture:
- Python version:
- ReproMin version (`repomin --version` or the installed package version):
- Backend (`host` or `docker`):

## Command and output

```sh
repomin /path/to/project \
  --command '...' \
  --match '...'
```

Include the relevant output and, when possible, a redacted `report.json`.

## What have you tried?

## Additional context
