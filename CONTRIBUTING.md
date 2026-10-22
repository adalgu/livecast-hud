# Contributing to LiveCast HUD

Thanks for taking the time to help. Bug reports, fixes, docs and small features are all
welcome. For anything large, please open an issue first so we can agree on the approach
before you spend time on it.

## Development setup

You need macOS and Python 3.10 or newer. From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'
```

Add the `gcp` extra (`'.[dev,gcp]'`) only if you work on the Google Cloud speech or
translation code. The tests do not need it.

## Running the checks

```sh
QT_QPA_PLATFORM=offscreen python3 -m pytest
ruff check .
```

`QT_QPA_PLATFORM=offscreen` lets the overlay window tests run without opening a window,
so they also work over SSH and in CI.

## Tests never touch the network

The test suite must run fully offline. `tests/conftest.py` installs a Python audit hook
that fails any test which opens a socket, builds a `urllib` request or starts a
subprocess. This keeps tests fast and deterministic, and it means running the tests can
never bill a Google Cloud project.

When you test code that talks to Google Cloud or the microphone, replace the client with
a fake (for example with `monkeypatch`) instead of calling the real service. Please do
not weaken or skip the audit hook to make a test pass.

## Before you open a pull request

- `pytest` and `ruff check .` pass locally.
- New behaviour has a test. Bug fixes have a test that failed before the fix.
- Code comments, docstrings and command-line help are in English.
- User-facing changes are listed under `## [Unreleased]` in `CHANGELOG.md`.
- If you changed a command-line option, environment variable, key or config field,
  update both `README.md` and `README.ko.md`, and `docs/configuration.md` if relevant.
- Keep each pull request focused on one change.

## Keep personal data out of issues and pull requests

Please do not attach your own recordings, talk scripts, glossaries, `--log` files or
screenshots of real slides. They often contain names, unpublished material or other
people's voices. To show a problem, reproduce it with the files in `examples/` or with a
short made-up script.

Never paste credentials, service-account keys, access tokens or Google Cloud project IDs.
See [SECURITY.md](SECURITY.md) for how to report a security problem privately.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE), as described in section 5 of the license.
