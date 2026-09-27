# Releasing DGF-Bench

Releases publish to [PyPI](https://pypi.org/project/dgf-bench/) automatically from a version tag,
using **PyPI Trusted Publishing** (OpenID Connect). No API token is ever stored — GitHub proves the
workflow's identity to PyPI directly. The workflow is [`.github/workflows/release.yml`](.github/workflows/release.yml).

## One-time setup (do this once)

On PyPI, tell the project to trust this GitHub workflow:

1. Sign in at [pypi.org](https://pypi.org) and open the project:
   **Your projects → dgf-bench → Manage → Publishing** (or, before the first automated release,
   *Account → Publishing → Add a pending publisher*).
2. Add a **GitHub** trusted publisher with exactly:
   - **Owner:** `jeremy1392`
   - **Repository:** `DGF-Bench`
   - **Workflow name:** `release.yml`
   - **Environment:** *(leave blank)*
3. Save.

That is the whole setup. (Optional hardening: create a GitHub environment named `pypi` under
**Settings → Environments**, add `environment: pypi` to the `publish` job, and set the same
environment name in the PyPI publisher.)

## Cut a release

1. Bump the version in **`src/dgf_bench/__init__.py`** (single source of truth), e.g. `0.1.0 → 0.1.1`.
   Update `CHANGELOG.md`.
2. Commit:
   ```bash
   git commit -am "Release 0.1.1"
   git push
   ```
3. Tag and push the tag — the tag must match the version, prefixed with `v`:
   ```bash
   git tag v0.1.1
   git push origin v0.1.1
   ```
4. The **Release to PyPI** workflow builds the wheel and sdist, checks that the tag equals the package
   version, and publishes. Watch it under the repository's **Actions** tab. Within a minute or two,
   `pip install dgf-bench==0.1.1` works.

A PyPI version number can be uploaded only once, so bump the version for every release.

## Manual fallback (no GitHub)

If you ever need to publish by hand:

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine upload dist/*          # username __token__, password = a PyPI API token
```
