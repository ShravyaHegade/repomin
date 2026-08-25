# FastAPI dependency regression fixture

This fixture models a small FastAPI route regression whose failure only keeps
occurring when the same dependency is declared in the project metadata and in
the nested runtime requirements file. The root requirements file also contains
an include, a constraint, an extra package index, and unrelated dependencies.
The test suite contains one unrelated failing test so the reducer must retain
the regression's failure identity rather than merely any non-zero exit.

The fixture is Docker-only because its oracle needs pinned FastAPI and pytest
versions. Build the local image from this directory; ReproMin never pulls an
image or runs a package resolver for you:

```sh
docker build -t repomin-fastapi-fixture benchmarks/python-fastapi

PYTHONPATH=src python3 -m repomin benchmarks/python-fastapi \
  --command 'python -m pytest -q' \
  --match 'FastAPI route regression: dependency override leaked' \
  --backend docker \
  --docker-image repomin-fastapi-fixture \
  --adapter python \
  --source-reducer none \
  --output /tmp/repomin-fastapi-result
```

The exported payload should retain the application, the test that exercises
`/checkout/42`, `pyproject.toml`, `requirements.txt`, and
`requirements/runtime.txt`, while removing the unrelated test and dependency
entries. The evidence is written to the sibling
`/tmp/repomin-fastapi-result.repomin` directory. Review [SECURITY.md](../../SECURITY.md)
before using the host backend or a Docker image built from untrusted input.
