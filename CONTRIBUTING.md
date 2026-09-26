# Contributing

Copy Editor is a local Python and JavaScript application. macOS and Linux are
supported through the Make targets; the optional `ce` launcher is for macOS.

1. Install Python 3.12+, uv, Node 20+, npm, and Make.
2. Run `make setup` and `make build` in a fresh checkout.
3. Run `make dev` for live CSS and JavaScript rebuilding and server reload.
4. Run `make test` before proposing a change. CI repeats setup, build, and
   tests on clean macOS and Linux checkouts.

Use temporary databases and invented prose in tests. Do not commit
`data/`, database or WAL files, exported documents, provider credentials,
or real prompts and responses. Backend tests can stub the model CLI; Node's
`node:test` suite in `tests/frontend/` covers browser-independent editor
logic. A real provider sign-in is not needed for automated tests.

Keep changes focused, explain their user-facing behavior, and describe the
verification performed. Include a regression test when fixing a data loss,
privacy, security, or concurrency issue. Before changing how document text
or dismissal history is sent to a model provider, update the Account copy and
README privacy section in the same change.

Add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md) for any change
a writer would notice, written for them rather than as a commit summary. Note
database changes under **Upgrade notes**.

## Releasing

Versions follow semantic versioning, and `pyproject.toml` holds the version.
From a clean, up-to-date `main` whose changelog has notes under **Unreleased**:

```bash
make release VERSION=0.3.0
```

This runs the tests, writes the version into `pyproject.toml`, `package.json`,
`package-lock.json`, and `uv.lock`, dates the changelog section, commits, and
creates an annotated `v0.3.0` tag. It does not push. Review the commit, then
push `main` and the tag together:

```bash
git push origin main v0.3.0
```

The tag runs the release workflow: the full CI on macOS and Linux, a check
that the tag matches the project version, and a GitHub Release whose notes are
that changelog section. Never move or reuse a pushed tag; fix mistakes in a
new patch release.

## Security

For potential security vulnerabilities, follow [SECURITY.md](SECURITY.md)
instead of posting exploit details in a public issue.
