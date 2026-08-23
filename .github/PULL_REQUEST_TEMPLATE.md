---
name: Pull request
about: Contribute a change to ReproMin
title: ""
labels: ""
assignees: ""
---

## Summary

What does this change do and why is it needed?

## Checklist

- [ ] Added or updated tests covering the change.
- [ ] Ran `ruff check src tests`.
- [ ] Ran `PYTHONPATH=src python3 -m compileall -q src tests`.
- [ ] Ran `PYTHONPATH=src python3 -m unittest discover -s tests`.
- [ ] Updated relevant documentation and the changelog.
- [ ] For reducer changes, added or updated a benchmark if applicable.
