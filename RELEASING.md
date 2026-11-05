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

## Publish to the MCP Registry

[`server.json`](server.json) lists the server on the [official MCP Registry](https://registry.modelcontextprotocol.io) as `ai.easydeploy/easydeploy-ai-mcp`. Each publish needs a new `version`; the registry refuses to overwrite an existing one. Publish after the PyPI upload has finished, because the registry checks the package on PyPI.

1. **`server.json`**: set `version` and each `packages[].version` to the release version. To list the PyPI package, add a `packages` entry once a release includes the README's `mcp-name` marker. Until then the file lists only the hosted endpoint:

   ```json
   "packages": [
     {
       "registryType": "pypi",
       "identifier": "easydeploy-ai-mcp",
       "version": "0.5.1",
       "transport": { "type": "stdio" },
       "environmentVariables": [
         { "name": "EDA_API_KEY", "description": "EasyDeploy API key", "isRequired": true, "isSecret": true }
       ]
     }
   ]
   ```

   `EDA_API_KEY` is the only variable stdio needs. The others in [Environment variables](README.md#environment-variables) are optional or apply only to HTTP.

2. **Log in with DNS.** The `v=MCPv1` TXT record at the apex of `easydeploy.ai` holds the public key, and the maintainer holds the Ed25519 private key. Keep the README's `mcp-name` marker: removing it breaks PyPI verification.

   ```bash
   mcp-publisher login dns --domain easydeploy.ai --private-key "$(openssl pkey -in <key.pem> -outform DER | tail -c 32 | xxd -p -c 64)"
   ```

3. **Validate and publish:**

   ```bash
   mcp-publisher validate server.json && mcp-publisher publish server.json
   ```
