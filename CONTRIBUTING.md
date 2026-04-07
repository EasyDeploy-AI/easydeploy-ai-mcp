# Contributing

Thanks for helping improve **easydeploy-ai-mcp**. This document covers how to report issues, set up a dev environment, and what we expect in pull requests.

## Reporting issues

- Use [GitHub Issues](https://github.com/easydeploy-ai/easydeploy-ai-mcp/issues) for bugs, feature requests, and documentation gaps.
- **Do not** file undisclosed security vulnerabilities as public issues. See [SECURITY.md](SECURITY.md) for responsible disclosure.

## Development setup

```bash
git clone https://github.com/easydeploy-ai/easydeploy-ai-mcp.git
cd easydeploy-ai-mcp
pip install -e ".[dev]"
pytest
```

You need a valid EasyDeploy **`EDA_API_KEY`** to exercise the live API in manual testing (optionally **`EDA_API_BASE`** if you are not hitting production). The test suite uses mocks where possible.

## Pull requests

- Keep changes focused and match existing style (types, naming, minimal comments).
- Run **`pytest`** before opening a PR.
- Update [CHANGELOG.md](CHANGELOG.md) (Unreleased / new version) when the change is user-visible.
- If you change tool names, HTTP behavior, or env vars, update **README.md** and any affected **docs/** files.

## API compatibility

Tool names, HTTP paths, and request/response handling in this repo should stay consistent with the **EasyDeploy public API** and the behavior described in **README.md** and **docs/**. If your PR changes how clients call the API or what agents see, note the API assumptions in the PR description.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Participation implies agreement to uphold it.
