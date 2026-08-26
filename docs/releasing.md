# Release process

Ostiari Escape Lab releases are built from an immutable tag on `main`.

## Prepare

1. Confirm the public scope and limitations remain accurate in `BETA.md`,
   `README.md`, `SECURITY.md`, and requirements traceability.
2. Update the version in `pyproject.toml` and `src/escape_lab/__init__.py`.
3. Move user-visible changes from `Unreleased` into a dated changelog section.
4. Update release-specific documentation and image tags.
5. Run:

   ```bash
   make lint
   make test
   python scripts/check_release.py --tag vVERSION
   python -m build
   ```

6. Merge through a pull request after all required checks pass.

## Publish

Create and push the exact version tag only from the reviewed `main` commit:

```bash
git tag -s vVERSION -m "Ostiari Escape Lab vVERSION"
git push origin vVERSION
```

The `Release` workflow:

- verifies that the tag, package version, module version, changelog, and beta
  classifier agree;
- runs lint and the complete standard-library regression suite;
- builds the wheel and source distribution;
- checks package metadata and installs the wheel in a fresh environment;
- creates SHA-256 checksums;
- records GitHub build provenance for the Python artifacts;
- creates a GitHub prerelease for alpha, beta, or release-candidate tags.

The workflow does not publish to PyPI. Configure a project-scoped PyPI Trusted
Publisher before adding that step; do not add a long-lived upload token.

## Verify

After publication:

1. Confirm the GitHub release contains the wheel, source distribution, and
   `SHA256SUMS`.
2. Verify an artifact:

   ```bash
   gh attestation verify ARTIFACT \
     --repo hk-775/OstiariEscapeLab
   ```

3. Install the attached wheel on a clean machine and run `escape-lab validate`
   and `escape-lab demo`.
4. Confirm CI, CodeQL, the gVisor gate, benchmark shadow campaign, and GitHub
   Pages are green for the release commit.
5. Preserve the release evidence and record any qualification exceptions in
   the release notes.
