# Changelog

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
