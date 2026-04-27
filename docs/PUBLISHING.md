# Publishing mymcp to PyPI

This document walks through the **first-ever** PyPI release for `mymcp`,
plus the steady-state release flow once the project is set up.

---

## One-time setup (do this once)

### 1. Create a PyPI account

1. Sign up at https://pypi.org/account/register/
2. Verify your email
3. **Enable 2FA** (required by PyPI for new accounts since 2024). Use an authenticator app (Aegis, 1Password, Google Authenticator) or a hardware key.

### 2. Claim the project name with a manual first upload

PyPI's Trusted Publisher (OIDC) flow requires the project to **already exist** before you can configure GitHub Actions to publish to it. So the very first upload has to be done manually.

You can do this either via [test.pypi.org](https://test.pypi.org/) first (recommended) or jump straight to production PyPI.

**Build the artifacts locally:**

```bash
# In the repo root, on the v2.0.0 tag (or master after merge)
git checkout master
git pull
rm -rf dist/
python -m pip install --upgrade build twine
python -m build
ls dist/
# expected: mymcp-2.0.0.tar.gz, mymcp-2.0.0-py3-none-any.whl
```

**Optional: dry-run on test.pypi.org**

1. Register a separate account at https://test.pypi.org (it's a fully separate user database from prod)
2. Generate an API token at https://test.pypi.org/manage/account/token/ — scope it to "All projects" for now
3. Save it locally:

   ```bash
   mkdir -p ~/.config
   cat > ~/.pypirc <<'EOF'
   [testpypi]
     username = __token__
     password = pypi-AgENdGV...   # paste the test.pypi.org token here
   EOF
   chmod 600 ~/.pypirc
   ```

4. Upload to test.pypi.org:

   ```bash
   twine upload --repository testpypi dist/*
   ```

5. Test the install from test.pypi.org in a throwaway venv:

   ```bash
   python -m venv /tmp/testenv
   /tmp/testenv/bin/pip install -i https://test.pypi.org/simple/ \
       --extra-index-url https://pypi.org/simple/ mymcp
   /tmp/testenv/bin/mymcp --version
   ```

   The `--extra-index-url` is needed because test.pypi.org doesn't mirror runtime deps like fastapi.

**Production upload:**

1. Generate a token at https://pypi.org/manage/account/token/ — scope to "All projects" for the first upload (you'll re-scope to project-only afterwards)
2. Add to `~/.pypirc`:

   ```ini
   [pypi]
     username = __token__
     password = pypi-AgEIcHlwaS5...   # the prod PyPI token
   ```

3. Upload:

   ```bash
   twine upload dist/*
   ```

4. Visit https://pypi.org/project/mymcp/ — you should see the project page

5. **Re-scope the token**: now that the project exists, go back to the token page, **delete the All-projects token**, and create a new one scoped specifically to `mymcp`. (Or skip this if you'll switch to OIDC immediately as in step 3 below — see next section.)

### 3. Configure Trusted Publisher (OIDC) for GitHub Actions

This is the secure replacement for storing PyPI tokens as GitHub secrets. Each release runs in a sandboxed environment and gets a short-lived OIDC token directly from PyPI.

1. Go to https://pypi.org/manage/project/mymcp/settings/publishing/
2. Click "Add a new publisher" → GitHub
3. Fill in:
   - **PyPI Project Name**: `mymcp`
   - **Owner**: `algony-tony`
   - **Repository name**: `mymcp`
   - **Workflow name**: `release.yml`
   - **Environment name**: `pypi`
4. Click "Add"

The workflow at `.github/workflows/release.yml` already declares this environment:

```yaml
publish-pypi:
  permissions:
    id-token: write
  environment: pypi
  steps:
    - uses: pypa/gh-action-pypi-publish@release/v1
```

After this is configured, you can **delete the API token** you used for the first upload — OIDC takes over from now on.

### 4. (Optional) Protect the `pypi` environment in GitHub

For an extra safety gate before publishing:

1. Go to repo → Settings → Environments → New environment → name it `pypi`
2. Add yourself (or trusted maintainers) under "Required reviewers"
3. The release workflow will pause and request approval before uploading

---

## Steady-state release flow

After one-time setup, every release is one command from your laptop:

```bash
# Make sure master is up to date and ready
git checkout master
git pull

# Confirm tests + lint clean locally (CI will also run them on the tag push)
pytest tests/ -v --benchmark-disable
ruff check . && ruff format --check . && mypy src/mymcp

# Update CHANGELOG: change the unreleased "## [2.0.0] - unreleased" header
# to a real date, e.g. "## [2.0.0] - 2026-04-27"
$EDITOR CHANGELOG.md
git add CHANGELOG.md
git commit -m "docs: cut 2.0.0 release"
git push

# Tag and push
git tag -a v2.0.0 -m "mymcp 2.0.0"
git push origin v2.0.0
```

That's it. The tag push triggers `.github/workflows/release.yml`, which:

1. Builds `dist/mymcp-2.0.0.tar.gz` + `mymcp-2.0.0-py3-none-any.whl`
2. Builds `mymcp-2.0.0-offline-bundle.tar.gz` (all wheels + ripgrep binaries)
3. Publishes to PyPI via OIDC (no secrets needed)
4. Creates a GitHub Release at https://github.com/algony-tony/mymcp/releases/tag/v2.0.0 with both artifacts attached and auto-generated release notes

Watch the run progress at https://github.com/algony-tony/mymcp/actions.

---

## Verifying a release

```bash
# Fresh install from PyPI in a throwaway venv
python -m venv /tmp/check
/tmp/check/bin/pip install --upgrade pip
/tmp/check/bin/pip install mymcp
/tmp/check/bin/mymcp --version
# expected: mymcp 2.0.0

/tmp/check/bin/mymcp doctor
# expected: python / mymcp / ripgrep / systemd lines, no errors

rm -rf /tmp/check
```

Smoke-test the offline bundle:

```bash
cd /tmp
curl -L -o bundle.tar.gz \
    https://github.com/algony-tony/mymcp/releases/download/v2.0.0/mymcp-2.0.0-offline-bundle.tar.gz
tar xzf bundle.tar.gz
cd mymcp-2.0.0-offline-bundle
ls wheels/   # should see mymcp + transitive deps as .whl files
ls -la ripgrep-*   # x86_64 + aarch64 binaries
```

---

## Patch release (e.g. 2.0.1)

For bugfix releases:

1. Open a PR fixing the bug, get it green and merged to master
2. Cut the patch:

   ```bash
   git checkout master && git pull
   $EDITOR CHANGELOG.md   # add "## [2.0.1] - YYYY-MM-DD" with bug list
   git commit -am "docs: cut 2.0.1 release"
   git push
   git tag -a v2.0.1 -m "mymcp 2.0.1"
   git push origin v2.0.1
   ```

3. Same workflow runs; users upgrade with `pipx upgrade mymcp`.

---

## Yanking a broken release

If a release is found to be broken after upload:

1. Visit https://pypi.org/project/mymcp/<version>/
2. Click "Manage" → "Options" → "Yank"
3. Provide a reason (gets shown to users)

Yanked releases stay installable for users who pin to that version (`mymcp==2.0.0`) but new `pipx install mymcp` will skip them.

You **cannot delete** a version from PyPI once uploaded (the version number is permanently consumed). To ship a fix, bump to the next patch version (2.0.1) and yank the broken one.

---

## Troubleshooting

**`mymcp` name was taken** by someone else before you registered:

If pypi.org shows another project at https://pypi.org/project/mymcp/, the name is gone. Pick a new name (e.g. `algony-mymcp`) and update:

- `pyproject.toml` `name = "..."`
- README install commands
- This document

**Trusted Publisher upload fails with `Token request failed`**:

- Confirm the workflow filename in PyPI settings matches `release.yml` exactly
- Confirm the environment name in PyPI settings matches the `environment: pypi` line in release.yml
- Confirm the repo's GitHub Actions has `permissions: id-token: write` (set in release.yml's `publish-pypi` job)
- Look at the action run's logs around the `pypa/gh-action-pypi-publish` step for the specific error

**Build fails locally with PEP 639 license error**:

`pyproject.toml` already uses SPDX `license = "Apache-2.0"`. If you re-add the legacy `License :: OSI Approved :: Apache Software License` classifier, setuptools 80+ will refuse to build. Don't add it.

**`setuptools-scm` fails with "no version found"**:

Ensure the build runs in a full git clone, not a shallow one. CI uses `fetch-depth: 0` for this. Locally, just `git clone` (no `--depth`).
