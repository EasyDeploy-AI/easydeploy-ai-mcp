# Releasing a new version

[`.github/workflows/publish-pypi.yml`](.github/workflows/publish-pypi.yml) uploads to PyPI when you **Publish** a **GitHub Release** (not a draft). The release tag must point at a commit whose `pyproject.toml` version matches the release.

**Prerequisites:** On PyPI, [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) lists this repo, workflow **`publish-pypi.yml`**, and environment **`pypi`**. On GitHub (**Settings → Environments**), create an environment named **`pypi`** (optionally restrict who can deploy to it). That name must match the `environment: pypi` job key in the workflow.

## Steps

1. **`pyproject.toml`** — set `[project].version` (e.g. `0.1.0`).
2. **`CHANGELOG.md`** — move `[Unreleased]` notes into a dated section for that version.
3. **Commit and push to `main`:**

   ```bash
   git checkout main && git pull origin main
   git add pyproject.toml CHANGELOG.md
   git commit -m "chore: release 0.1.0"
   git push origin main
   ```

4. **Tag** — use `v` + the same semver as `pyproject.toml`:

   ```bash
   git tag -a v0.1.0 -m "v0.1.0"
   git push origin v0.1.0
   ```

5. **GitHub** — **Releases** → **Draft a new release** → select that tag → **Publish release**.

### Optional: check the build locally

```bash
pip install build && python -m build && ls dist/
```

### Fallback: upload from your machine

Requires a PyPI API token (or `~/.pypirc`):

```bash
pip install build twine && python -m build && twine upload dist/*
```
