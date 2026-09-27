# Releasing DGF-Bench

Every version is published from a **GitHub Release**, so each one has release notes on
[github.com/jeremy1392/DGF-Bench/releases](https://github.com/jeremy1392/DGF-Bench/releases).
Publishing the release runs [`.github/workflows/release.yml`](.github/workflows/release.yml), which builds
the package, checks that the tag matches the version, publishes to [PyPI](https://pypi.org/project/dgf-bench/)
with **Trusted Publishing** (no API token stored anywhere) and attaches the wheel and sdist to the release.

## One-time setup

On PyPI, tell the project to trust the workflow: **Your projects → dgf-bench → Manage → Publishing →
Add a GitHub publisher** with exactly:

- **Owner:** `jeremy1392`
- **Repository:** `DGF-Bench`
- **Workflow name:** `release.yml`
- **Environment:** *(leave blank)*

## Cut a release

1. Bump the version in **`src/dgf_bench/__init__.py`** (single source of truth), e.g. `0.1.1 → 0.1.2`.
2. In **`CHANGELOG.md`**, rename `## Unreleased` to `## 0.1.2 — <date>`: this section becomes the release notes.
3. Commit and push:
   ```bash
   git commit -am "Release 0.1.2"
   git push
   ```
4. Create the release with its notes (the tag is `v` + the version):
   ```bash
   python tools/release_notes.py 0.1.2 > notes.md
   gh release create v0.1.2 --title "DGF-Bench 0.1.2" --notes-file notes.md
   ```
   Or on GitHub: **Releases → Draft a new release**, tag `v0.1.2`, paste the notes, **Publish release**.
   A release saved as a draft publishes nothing until you press **Publish release**.
5. The **Release** workflow publishes to PyPI and attaches the files (watch it under **Actions**). A minute
   later, `pip install dgf-bench==0.1.2` works.

A PyPI version number can be uploaded only once, so bump the version for every release. Re-publishing
a release whose version is already on PyPI leaves PyPI unchanged (`skip-existing`).

## Manual fallback

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine upload dist/*          # username __token__, password = a PyPI API token
```

Then create the GitHub release with its notes as in step 4, so the version still has release notes.
