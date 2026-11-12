# Changelog

## 1.3.0 — read before you install, enforce on the way in

**The preview shows the template, not counts.** Install → Preview now displays:

- the full text of `SOUL.md` ;
- each skill with its own description and a **Read** button (grouped `skills/<category>/<name>` skills are listed too) ;
- every routine with its schedule, prompt and script ;
- every MCP server — from `config.yaml` (`mcp_servers`, what Hermes starts) and from `mcp.json` — with the exact command line or URL ;
- the settings that change what the agent may do (`approvals`, `hooks`, `quick_commands`, `terminal`, `tools`, a model `base_url`) ;
- the plugins that installing will enable ;
- every file of the bundle, each one openable.

New route `POST /bundle-file`. The CLI does the same: `hermes template inspect` prints the same plan, and `--file SOUL.md` prints one file in full.

**The installer enforces what the packer promises, on bundles nobody packed:**

- Routines go through Hermes' own `cron.job_definition.import_job_definitions` (the call `hermes profile install` uses). They arrive paused with a reason attached; `jobs.json` is never written by hand. An update keeps the user's scheduler state and never deletes their cron store.
- The shipped `config.yaml` is sanitized again on install: provider kept, endpoint, account and keys removed.
- MCP servers arrive off: `enabled: false` under `mcp_servers` in `config.yaml` (the flag Hermes honours) and in `mcp.json`.
- A bundle with routines refuses to install outside a Hermes runtime instead of dropping them silently.
- The checksum index is checked on the extracted bytes *before* the installer rewrites anything.
- A failed routine import rolls back a fresh install.

**Core helpers instead of private copies:**

- The tombstone is cleared with `hermes_constants.clear_named_profile_deleted`.
- Key presence comes from the target profile's own credential scope (`build_profile_secret_scope`). The installer never opens a `.env`, reads no `os.environ`, and a profile that does not exist yet has no keys.

**Wording.** The plugin enables plugins *already installed on the machine* unless you pass `--no-enable-plugins`; the docs no longer claim "never auto-enabled".

77 tests (70 without a Hermes install, 7 skipped), including a set of hand-assembled hostile bundles (`tests/test_install_enforces.py`).

## 1.2.4 — catalog-clean

- The settings value is read through the plugin context (`ctx.get_config`), bound at
  `register()`, instead of importing the private `hermes_cli.plugins._plugin_settings_entry`.
  One validator warning gone; a context that raises falls back to the default.
- The only remaining validator warning is `agent.redact.redact_sensitive_text`, which the
  current docs list on the **Public plugin API** page (the validator table in this build is
  older than that entry).
- 65 tests.

## 1.2.3 — publish with the profile's own token

- The publish routes read the named profile's credentials (``?profile=``, which every
  desktop ``ctx.rest`` call already carries) through
  ``build_profile_secret_scope`` — the same helper the dashboard's profile routes use.
  Without it ``get_secret`` ran unscoped and, in a gateway serving several profiles,
  raised instead of borrowing: the page said "No GitHub token" while the token sat in
  that profile's ``.env``.
- A profile name from the query string is matched against the profiles directory, so
  ``..``, a slash or a tombstoned name resolves to nothing.
- 63 tests.

## 1.2.2 — a settings page in the Desktop

- `config_schema` in `plugin.yaml`: the plugin now shows up under **Settings →
  Plugins** in the Desktop app, with two real fields — *Publish repository*
  (`owner/name`, empty means `agent-templates`) and *GitHub token* (`secret`,
  stored in `.env` as `GITHUB_TOKEN`, never in `config.yaml`).
- The publish default repository is read from that setting, with the explicit
  argument still winning and the built-in default last.
- Verified through `hermes_cli.plugins_settings.plugin_settings_fields`, the same
  function the Desktop reads: two fields, correct types, secret carrying only its
  env name and a presence flag.
- 61 tests.

## 1.2.1 — readable in the dark

- Fields were painted with `--ui-bg-input`, a token defined once and never
  overridden by the dark theme: white boxes with light text on a dark page. Fields
  now use tokens that follow the theme, with their own placeholder and focus rules.
- `--ui-border`, `--ui-success`, `--ui-warning`, `--ui-danger` do not exist in the
  app; mapped to the real names (`--ui-stroke-primary`, `--ui-green`,
  `--ui-yellow`, `--ui-red`), so borders and status colours actually render.
- The page reflows on a container query: in a narrow slot (measured 520 px) the
  agent list and the form stack instead of squeezing the fields to 87 px.
- Two French placeholders in an otherwise English UI, now English.

## 1.2.0 — the link

- **Publish**: upload a bundle to a GitHub repository and get one permanent raw
  link per agent. Plan first (read-only), then an explicit yes; the repository is
  never created silently, and a private one is refused.
- **The link is proven**: the published URL is downloaded back through the same
  guarded fetcher an installer uses and compared byte for byte.
- **Install from a link is the normal path**: the page previews it as soon as it is
  pasted, and the CLI takes it as a first-class source.
- **Updates by the same link**: the source URL is remembered in the installed
  profile, so reopening it offers `Update v1.0.0 → v1.1.0` and keeps memories,
  sessions and `.env`.
- **URL guard** (`netsafe`): HTTPS and public hosts only on the page, no
  credentials in the URL, every redirect re-checked (no jump into the private
  network, no `https`→`http` downgrade), 20 MB cap, cached downloads pruned.
- **Measured**: `raw.githubusercontent.com` caches for up to 5 minutes per client,
  so the result also gives the commit-pinned URL.
- Reported file count now includes `template.yaml`, so the card, the archive and
  the preview agree.
- Tests: 58 (was 23), including a fake GitHub for the publish path.

## 1.1.0

- Sibling-profile discovery when the gateway runs inside a profile.
- Idempotent redaction; CLI flag collisions (`--out`, `--template-version`).
- `default` is published as `my-agent` instead of being refused as reserved.
- Screenshots and a development loop in the README.
