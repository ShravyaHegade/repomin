# Pipenv manifest benchmark

This fixture exercises the network-free `Pipfile` adapter. The oracle keeps
`required-package` while allowing unrelated runtime, development, and Python
version option entries to be removed. Pipenv source settings remain untouched.

Run from the repository root:

```sh
out_parent="$(mktemp -d /tmp/repomin-pipenv-package.XXXXXX)"
PYTHONPATH=src python3 -m repomin benchmarks/pipenv-package \
  --command 'python3 reproduce.py' \
  --match 'ORIGINAL_FAILURE' \
  --adapter pipenv \
  --source-reducer none \
  --output "$out_parent/result"
```
