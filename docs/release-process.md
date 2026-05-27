# Release Process

This document explains how `byline-audit` is released to PyPI. The release
workflow lives at [`.github/workflows/release.yml`](../.github/workflows/release.yml)
and triggers on any pushed tag matching `v*.*.*`.

## 1. Initial PyPI trusted-publisher setup (one-time)

We use [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
via GitHub Actions OIDC, so no API tokens are stored in the repository.

To configure it once:

1. Visit https://pypi.org/manage/project/byline-audit/settings/publishing/
2. Click **Add a new publisher** and choose **GitHub**.
3. Fill in:
   - **Owner**: `rupivbluegreen`
   - **Repository name**: `byline`
   - **Workflow filename**: `release.yml`
   - **Environment name**: *(leave blank for now; set if we adopt
     deployment environments later)*
4. Save.

After this is configured once, all future tagged releases publish
automatically with no API tokens.

## 2. Cutting a release

1. Bump `__version__` in `byline/__init__.py` and `version` in
   `pyproject.toml`. They must match.
2. Add a CHANGELOG.md entry under a new `## [x.y.z] - YYYY-MM-DD`
   heading. The release workflow extracts notes between this heading
   and the next `## [` heading.
3. Commit the bump:
   ```bash
   git commit -am "chore: release v0.x.y"
   ```
4. Tag it:
   ```bash
   git tag v0.x.y -m "v0.x.y"
   ```
5. Push commit and tag:
   ```bash
   git push && git push --tags
   ```
6. The `release.yml` workflow runs automatically. It builds the sdist
   and wheel, publishes to PyPI via OIDC, then creates a GitHub Release
   with the extracted changelog notes and uploaded dist files.
7. Verify the release on:
   - PyPI: https://pypi.org/project/byline-audit/
   - GitHub Releases: https://github.com/rupivbluegreen/byline/releases

## 3. Fallback if trusted publishing isn't configured

If the trusted publisher entry hasn't been added on PyPI yet, the
`pypi-publish` job will fail. You can either:

- Configure trusted publishing (see section 1) and re-run the workflow,
  or
- Fall back to a manual upload:
  ```bash
  python -m build
  python -m twine upload dist/*
  ```
  using a project-scoped PyPI API token stored in your local environment.

## 4. CI is independent

The existing `ci.yml` workflow (lint, format, test matrix, base-install
verification) runs on every push and pull request and is independent
of this release workflow. Failing CI does not block a tag-triggered
release, so make sure `main` is green before tagging.

## 5. Yanking a release

If a release needs to be pulled (security issue, broken build, accidental
upload), yank it from
`https://pypi.org/manage/project/byline-audit/release/<version>/`.

Note that PyPI **does not allow re-publishing the same version number**.
If `0.2.0` is broken, yank it and ship `0.2.1` with the fix; do not try
to overwrite `0.2.0`.
