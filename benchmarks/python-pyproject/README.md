# Python `pyproject.toml` benchmark

This network-free fixture exercises the Python manifest adapter against the
dependency declaration forms supported by ReproMin: PEP 621 dependencies and
optional dependencies, build requirements, Poetry and PDM tables, uv
development dependencies, and dependency groups.

The oracle requires only the project name and `repomin-required==1.0`. Every
other dependency declaration is intentionally removable. Run the fixture from
the repository root with:

```sh
PYTHONPATH=src python3 -m repomin benchmarks/python-pyproject \
  --command "python3 reproduce.py" \
  --match ORIGINAL_FAILURE \
  --adapter python \
  --source-reducer none \
  --output /tmp/repomin-python-pyproject-result
```

The exported `pyproject.toml` should retain the required dependency and omit
all `repomin-unused-*` entries. Independently running `python3 reproduce.py`
must still exit with status 1 and print `ORIGINAL_FAILURE`.
