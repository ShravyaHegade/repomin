# Releasing ReproMin

This checklist describes the supported GitHub Release process for the
pre-alpha project. ReproMin is not published to PyPI; do not add a PyPI upload
step without an explicit maintainer decision.

## Before Tagging

1. Confirm the working tree is clean and the default branch is up to date.
2. Update `src/repomin/__init__.py` with the release version. Keep the version
   consistent with the Git tag and the wheel/sdist filenames.
3. Move the relevant entries from the `Unreleased` section of `CHANGELOG.md`
   into a dated version heading. Include user-visible behavior, compatibility
   notes, and known limitations.
4. Update the installation URL in `README.md` and
   `docs/QUICKSTART.zh-CN.md` when the release asset name changes.
5. Run the full local verification from the repository root:

   ```sh
   python -m ruff check src tests
   python -m compileall -q src tests
   PYTHONPATH=src python -m unittest discover -s tests
   python3 benchmarks/run_offline.py --json-output /tmp/repomin-benchmark-results.json
   ```

   The benchmark JSON must report zero failed checks. Skips are acceptable only
   when the missing optional tool is understood and documented.

## Build And Verify

Build into a clean `dist/` directory and verify both artifacts:

```sh
rm -rf dist build
python -m build
python -m twine check dist/*
```

Install the wheel and source distribution in separate temporary virtual
environments. Run `repomin --help`, `repomin --version`, and the complete test
suite from outside the source tree. Confirm that the installed package imports
from its virtual environment rather than from the checkout.

Record SHA-256 checksums before uploading:

```sh
shasum -a 256 dist/*
```

Use `sha256sum` instead of `shasum` on Linux systems that do not provide the
BSD command.

## GitHub Release

Create an annotated tag from the verified commit and push it:

```sh
git tag -a vX.Y.Z -m "ReproMin vX.Y.Z"
git push origin vX.Y.Z
```

Create a GitHub Release for that tag and attach exactly the wheel and source
distribution produced by the verification step. Copy the changelog section
into the release notes and include the checksums. Do not rebuild the artifacts
after recording checksums.

After publishing, verify that:

- the release page links to the expected tag and assets;
- the README and both quick-start installation links resolve;
- a clean virtual environment can install each asset;
- `repomin --version` reports the released version; and
- the release CI run is successful.

If an artifact is wrong, mark the release as a draft or remove the incorrect
asset before users install it. Never overwrite an existing release asset with a
different file under the same name.
