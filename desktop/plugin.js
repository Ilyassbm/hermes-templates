// hermes-templates — Desktop half: a Templates page with a Share tab and an Install tab.
//
// Everything the page shows comes from this package's own backend
// (dashboard/plugin_api.py) through ctx.rest. The bundle itself never leaves the
// machine unless the person clicks Download, and the page cannot install
// anything the preview did not show first.
import { host, haptic, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'
import { jsx as runtimeJsx, jsxs as runtimeJsxs } from 'react/jsx-runtime'

// The automatic runtime's third argument is the React *key*, not children —
// passing an array there silently renders an empty element. These two wrappers
// take children third, the way this file is written, and hand them to the
// runtime under `props.children`.
const jsx = (type, props, children) =>
  runtimeJsx(type, children === undefined ? props : { ...props, children })
const jsxs = (type, props, children) =>
  runtimeJsxs(type, children === undefined ? props : { ...props, children })

const tap = (kind) => {
  try {
    haptic(kind)
  } catch (e) {
    /* haptics are decorative: an unknown kind must never break a click */
  }
}

let rest = null
let osDoor = null // ctx.os: clipboard + open-external, scoped by the host

const ROUTES = ROUTES_AREA || 'routes'
const SIDEBAR_NAV = SIDEBAR_NAV_AREA || 'sidebar.nav'

// Only tokens the app actually defines, and only ones redefined in the dark
// theme: `--ui-bg-input` is set once for light (`#fcfcfc`) and never overridden,
// which painted white fields with light text on a dark page. Field chrome comes
// from the app's own `desktop-input-chrome` class instead (see FIELD_CLASS).
const T = {
  text: 'var(--ui-text-primary)',
  dim: 'var(--ui-text-secondary)',
  faint: 'var(--ui-text-tertiary)',
  border: 'var(--ui-stroke-primary)',
  stroke: 'var(--ui-stroke-secondary)',
  card: 'var(--ui-bg-card)',
  raised: 'var(--ui-bg-elevated)',
  code: 'var(--ui-inline-code-background)',
  codeText: 'var(--ui-inline-code-foreground)',
  accent: 'var(--ui-accent)',
  ok: 'var(--ui-green)',
  warn: 'var(--ui-yellow)',
  bad: 'var(--ui-red)'
}

// The app's own `desktop-input-chrome` is `--dt-input-bg: 0%` / `--dt-input-border: 4%`
// in this build: fields read as plain text. We paint from the theme tokens instead
// (all redefined in the dark theme), and inject the placeholder/focus rules that
// inline styles cannot express.
const FIELD_CLASS = 'ht-field'
const FIELD_CSS = `
.ht-page { container-type: inline-size; }
.ht-grid { display: grid; gap: 18px; grid-template-columns: minmax(170px, 230px) minmax(0, 1fr); align-items: start; }
.ht-pair { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
.ht-panel { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.ht-list { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
/* The host hands a page whatever width its slot has (measured: 520px here, wide in the
   app's own window). Collapse to one column rather than squeezing the fields. */
@container (max-width: 700px) {
  .ht-grid { grid-template-columns: minmax(0, 1fr); }
}
.ht-field { background: var(--ui-bg-elevated); border: 1px solid var(--ui-stroke-primary); color: var(--ui-text-primary); }
.ht-field::placeholder { color: var(--ui-text-tertiary); opacity: 1; }
.ht-field:hover { border-color: var(--ui-stroke-secondary); }
.ht-field:focus, .ht-field:focus-visible {
  border-color: var(--ui-accent); outline: none;
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--ui-accent) 28%, transparent);
}
`

function injectFieldCss() {
  if (document.getElementById('ht-field-css')) return
  const style = document.createElement('style')
  style.id = 'ht-field-css'
  style.textContent = FIELD_CSS
  document.head.appendChild(style)
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

// Text on a filled accent button. `--ui-bg-primary` is a *background* token and
// turns into a translucent tint here (measured: 24% alpha on the accent, so the
// label vanished). Pick black or white from the accent's own luminance instead,
// which holds in both themes and for a retinted accent.
let inkCache = null
function accentInk() {
  if (inkCache) return inkCache
  try {
    const probe = document.createElement('div')
    probe.style.cssText = 'position:absolute;left:-9999px;top:0;background:var(--ui-accent)'
    document.body.appendChild(probe)
    const rgb = getComputedStyle(probe).backgroundColor
    probe.remove()
    const parts = (rgb.match(/[\d.]+/g) || []).slice(0, 3).map(Number)
    const luminance = (0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]) / 255
    inkCache = luminance > 0.6 ? '#0b0b0c' : '#ffffff'
  } catch (e) {
    inkCache = '#ffffff'
  }
  return inkCache
}

const box = (props, children) => jsx('div', { ...props, children })

const S = {
  page: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, color: T.text },
  header: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '14px 20px', borderBottom: `1px solid ${T.border}`, flexShrink: 0
  },
  body: { flex: 1, minHeight: 0, overflowY: 'auto', padding: '18px 20px 28px' },
  h1: { fontSize: 15, fontWeight: 600, letterSpacing: '-0.01em' },
  sub: { fontSize: 12, color: T.faint, marginTop: 2 },
  card: {
    background: T.card, border: `1px solid ${T.border}`, borderRadius: 10, padding: 14
  },
  label: { fontSize: 11, fontWeight: 500, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.04em' },
  input: {
    width: '100%', boxSizing: 'border-box', background: T.raised,
    border: `1px solid ${T.border}`, color: T.text, borderRadius: 6,
    padding: '7px 9px', fontSize: 13, fontFamily: 'inherit', outline: 'none'
  },
  btn: (primary, disabled) => ({
    appearance: 'none', border: `1px solid ${primary ? 'transparent' : T.stroke}`,
    background: primary ? T.accent : 'transparent',
    color: primary ? accentInk() : T.text,
    borderRadius: 7, padding: '6px 12px', fontSize: 12.5, fontWeight: 550,
    fontFamily: 'inherit', cursor: disabled ? 'default' : 'pointer',
    opacity: disabled ? 0.45 : 1
  }),
  chip: {
    display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11,
    padding: '2px 7px', borderRadius: 999, border: `1px solid ${T.stroke}`, color: T.dim,
    marginRight: 4, marginBottom: 4
  }
}

// ---------------------------------------------------------------- helpers

function bytes(n) {
  if (!n && n !== 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function Field({ label, hint, children }) {
  return box({ style: { display: 'flex', flexDirection: 'column', gap: 5, minWidth: 0 } }, [
    box({ key: 'l', style: S.label }, label),
    children,
    hint ? box({ key: 'h', style: { fontSize: 11, color: T.faint } }, hint) : null
  ])
}

function Toggle({ label, hint, checked, onChange, warn }) {
  return jsxs('label', {
    style: {
      display: 'flex', alignItems: 'flex-start', gap: 9, padding: '7px 0',
      cursor: 'pointer', borderTop: `1px solid ${T.border}`
    },
    children: [
      jsx('input', {
        key: 'i', type: 'checkbox', checked, onChange: (e) => onChange(e.target.checked),
        style: { marginTop: 2, accentColor: T.accent, width: 14, height: 14 }
      }),
      jsxs('span', {
        key: 't', style: { display: 'flex', flexDirection: 'column', gap: 1 },
        children: [
          jsx('span', { key: 'n', style: { fontSize: 13, color: warn ? T.warn : T.text } }, label),
          hint ? jsx('span', { key: 'h', style: { fontSize: 11, color: T.faint, lineHeight: 1.45 } }, hint) : null
        ]
      })
    ]
  })
}

function Chip({ children, tone }) {
  const color = tone === 'ok' ? T.ok : tone === 'warn' ? T.warn : tone === 'bad' ? T.bad : T.dim
  return box({ style: { ...S.chip, color, borderColor: tone ? color : T.stroke } }, children)
}

function Notice({ tone, children }) {
  const color = tone === 'bad' ? T.bad : tone === 'warn' ? T.warn : T.dim
  return box({
    style: {
      borderLeft: `2px solid ${color}`, paddingLeft: 10, fontSize: 12, color,
      lineHeight: 1.5, marginTop: 6
    }
  }, children)
}

function Button({ children, onClick, primary, disabled, title }) {
  return jsx('button', {
    type: 'button', onClick, disabled, title,
    style: S.btn(primary, disabled),
    children
  })
}

async function copy(text) {
  try {
    if (osDoor && typeof osDoor.writeClipboard === 'function') {
      if (await osDoor.writeClipboard(text)) {
        tap('tap')
        return true
      }
    }
    await navigator.clipboard.writeText(text)
    tap('tap')
    return true
  } catch (e) {
    return false
  }
}

function openLink(url) {
  try {
    if (osDoor && typeof osDoor.openExternal === 'function') return osDoor.openExternal(url)
  } catch (e) {
    /* fall through */
  }
  return window.open(url, '_blank', 'noopener,noreferrer')
}

// The gateway socket arrives after the first render on Desktop: the first RPC
// would throw "gateway unavailable" and paint a dead page. Retry it.
async function requestReady(fn, tries = 6) {
  let last = null
  for (let i = 0; i < tries; i += 1) {
    try {
      return await fn()
    } catch (error) {
      last = error
      const message = String((error && error.message) || error)
      if (!/gateway unavailable|not connected|socket/i.test(message)) throw error
      await new Promise((resolve) => setTimeout(resolve, [200, 400, 800, 1600, 3200, 4000][i] || 4000))
    }
  }
  throw last
}

function downloadBlob(name, base64) {
  const binary = atob(base64)
  const buffer = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) buffer[i] = binary.charCodeAt(i)
  const blob = new Blob([buffer], { type: 'application/octet-stream' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 4000)
}

function readFileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('could not read that file'))
    reader.onload = () => {
      const result = String(reader.result || '')
      resolve(result.includes(',') ? result.split(',').pop() : result)
    }
    reader.readAsDataURL(file)
  })
}

// ---------------------------------------------------------------- Publish (the link)

const errText = (e) => String((e && e.message) || e).replace(/^\d{3}:\s*/, '').replace(/^\{"detail":"(.*)"\}$/, '$1')

function CopyButton({ text, label, done }) {
  const [state, setState] = useState(false)
  return jsx(Button, {
    onClick: async () => {
      const ok = await copy(text)
      setState(ok)
      setTimeout(() => setState(false), 1600)
    }
  }, state ? (done || 'Copied') : label)
}

// Share ▸ Publish: read the plan, show exactly what will be written where, ask once, then hand back the link.
function PublishPanel({ templateId, onPublished }) {
  const [phase, setPhase] = useState('idle') // idle | planning | confirm | publishing | done
  const [plan, setPlan] = useState(null)
  const [out, setOut] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => { setPhase('idle'); setPlan(null); setOut(null); setError('') }, [templateId])

  const start = async () => {
    setPhase('planning')
    setError('')
    try {
      const data = await requestReady(() => rest('/publish/plan', { method: 'POST', body: { id: templateId } }))
      setPlan(data)
      setPhase('confirm')
    } catch (e) {
      setError(errText(e))
      setPhase('idle')
    }
  }

  const confirm = async () => {
    setPhase('publishing')
    setError('')
    try {
      const data = await rest('/publish', {
        method: 'POST',
        body: { id: templateId, create_repo: !!(plan && plan.creates_repo) }
      })
      setOut(data)
      setPhase('done')
      tap('success')
      if (onPublished) onPublished(data)
    } catch (e) {
      setError(errText(e))
      setPhase('confirm')
    }
  }

  if (phase === 'done' && out) {
    return jsxs('div', {
      style: { borderTop: `1px solid ${T.border}`, paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }
    }, [
      jsxs('div', { key: 'h', style: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' } }, [
        jsx('span', { key: 't', style: { fontSize: 13, fontWeight: 600, color: T.ok } },
          out.updated ? `Updated v${out.version} — same link` : `Published v${out.version}`),
        out.created_repo ? jsx(Chip, { key: 'r' }, `created ${out.repo_full}`) : null,
        jsx(Chip, { key: 'v', tone: out.verified ? 'ok' : 'warn' }, out.verified ? 'link checked' : 'link not checked')
      ]),
      jsx('code', {
        key: 'u',
        style: {
          fontFamily: 'var(--ui-font-mono, monospace)', fontSize: 11.5, color: T.codeText, background: T.code,
          border: `1px solid ${T.stroke}`, borderRadius: 6, padding: '7px 9px', wordBreak: 'break-all', userSelect: 'all'
        }
      }, out.url),
      jsxs('div', { key: 'b', style: { display: 'flex', gap: 8, flexWrap: 'wrap' } }, [
        jsx(CopyButton, { key: 'c', text: out.url, label: 'Copy link' }),
        jsx(CopyButton, { key: 'm', text: out.share_text, label: 'Copy message', done: 'Message copied' }),
        jsx(Button, { key: 'o', onClick: () => openLink(out.page) }, 'View on GitHub')
      ]),
      out.propagation_note ? jsx(Notice, { key: 'pn', tone: 'warn' }, out.propagation_note) : null,
      out.propagation_note ? jsx('div', { key: 'pl' }, jsx(CopyButton, { text: out.pinned_url, label: 'Copy the immediate link', done: 'Copied' })) : null,
      jsx('div', { key: 'n', style: { fontSize: 11.5, color: T.faint, lineHeight: 1.55 } },
        'Whoever has the link pastes it in Templates ▸ Install (or runs hermes template install). Publish again to update it — the link stays the same.')
    ])
  }

  if (phase === 'confirm' && plan) {
    const blocked = !plan.ready
    return jsxs('div', {
      style: { borderTop: `1px solid ${T.border}`, paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 7 }
    }, [
      jsx('div', { key: 'l', style: S.label }, 'Publish — check before it goes out'),
      blocked
        ? jsx('div', { key: 'p', style: { display: 'flex', flexDirection: 'column' } },
            plan.problems.map((problem, i) => jsx(Notice, { key: i, tone: 'bad' }, problem)))
        : jsxs('div', { key: 'd', style: { display: 'flex', flexDirection: 'column', gap: 3, fontSize: 12, color: T.dim, lineHeight: 1.55 } }, [
            jsx('div', { key: '1' }, `Writes ${plan.path} (+ a README) to ${plan.repo_full}, branch ${plan.branch}.`),
            jsx('div', { key: '2' }, plan.overwrites ? 'Replaces the version at this link; everyone using it will see an update.' : 'New file.'),
            plan.creates_repo
              ? jsx(Notice, { key: '3', tone: 'warn' }, `${plan.repo_full} does not exist: it will be created as a PUBLIC repository.`)
              : null,
            jsx('div', { key: '4' }, ['Link: ', jsx('code', { key: 'c', style: { fontFamily: 'var(--ui-font-mono, monospace)', fontSize: 11, wordBreak: 'break-all', overflowWrap: 'anywhere' } }, plan.url)]),
            jsx('div', { key: '5', style: { color: T.faint } },
              `Anyone with the link can read the ${plan.files} files in this bundle (${bytes(plan.bytes)}). It holds no credentials, sessions or history.`)
          ]),
      plan.needs_token
        ? jsx('div', { key: 'cmd' }, jsx(CopyButton, { text: 'hermes config set GITHUB_TOKEN <your token>', label: 'Copy the command', done: 'Copied' }))
        : null,
      error ? jsx(Notice, { key: 'e', tone: 'bad' }, error) : null,
      jsxs('div', { key: 'a', style: { display: 'flex', gap: 8 } }, [
        blocked ? null : jsx(Button, { key: 'go', primary: true, onClick: confirm }, plan.creates_repo ? 'Create repository & publish' : 'Publish'),
        jsx(Button, { key: 'x', onClick: () => { setPhase('idle'); setError('') } }, blocked ? 'Close' : 'Cancel')
      ])
    ])
  }

  return jsxs('div', {
    style: { borderTop: `1px solid ${T.border}`, paddingTop: 12, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }
  }, [
    jsx(Button, {
      key: 'p', primary: true, onClick: start,
      disabled: phase === 'planning' || phase === 'publishing'
    }, phase === 'planning' ? 'Checking…' : phase === 'publishing' ? 'Publishing…' : 'Publish & get a link'),
    jsx('span', { key: 'h', style: { fontSize: 11.5, color: T.faint } }, 'One link anyone can paste in Install.'),
    error ? jsx(Notice, { key: 'e', tone: 'bad' }, error) : null
  ])
}

// ---------------------------------------------------------------- Share

const BLANK = { name: '', title: '', description: '', author: '', license: '', version: '1.0.0' }

function ShareTab({ profiles, onCreated }) {
  const [selected, setSelected] = useState(profiles[0] ? profiles[0].id : '')
  const [form, setForm] = useState(BLANK)
  const [options, setOptions] = useState({ skills: true, cron: true, mcp: true, plugins: true, memories: false })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [copied, setCopied] = useState('')

  const profile = profiles.find((p) => p.id === selected) || null

  useEffect(() => {
    if (!profile) return
    setForm({
      name: profile.suggested_name || profile.id,
      title: profile.description || profile.id,
      description: profile.description || '',
      author: form.author,
      license: form.license,
      version: '1.0.0'
    })
    setResult(null)
    setError('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected])

  const set = (key) => (event) => setForm((prev) => ({ ...prev, [key]: event.target.value }))
  const toggle = (key) => (value) => setOptions((prev) => ({ ...prev, [key]: value }))

  const create = async () => {
    if (!profile || busy) return
    setBusy(true)
    setError('')
    try {
      const payload = await requestReady(() => rest('/create', {
        method: 'POST',
        body: ({
          profile: profile.id,
          ...form,
          skills: options.skills,
          cron: options.cron,
          mcp: options.mcp,
          plugins: options.plugins,
          memories: options.memories
        })
      }))
      setResult(payload)
      tap('success')
      if (onCreated) onCreated()
    } catch (e) {
      setError(String((e && e.message) || e))
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    if (!result) return
    try {
      const payload = await rest(`/download/${result.id}`, { method: 'POST', body: {} })
      downloadBlob(payload.filename, payload.data_base64)
    } catch (e) {
      setError(String((e && e.message) || e))
    }
  }

  if (!profiles.length) {
    return box({ style: { ...S.card, color: T.faint, fontSize: 13 } }, 'No profile to share yet — create one in Hermes first.')
  }

  return jsxs('div', {
    className: 'ht-grid',
    children: [
      box({ key: 'list', className: 'ht-list' }, [
        box({ key: 'l', style: { ...S.label, marginBottom: 6 } }, 'Your agents'),
        ...profiles.map((p) => jsxs('button', {
          key: p.id,
          type: 'button',
          onClick: () => { tap('tap'); setSelected(p.id) },
          style: {
            appearance: 'none', textAlign: 'left', fontFamily: 'inherit', cursor: 'pointer',
            padding: '8px 10px', borderRadius: 8,
            border: `1px solid ${p.id === selected ? T.stroke : 'transparent'}`,
            background: p.id === selected ? T.raised : 'transparent',
            color: T.text, display: 'flex', flexDirection: 'column', gap: 3
          },
          children: [
            jsx('span', { key: 'n', style: { fontSize: 13, fontWeight: 550 } }, p.label),
            jsx('span', {
              key: 'm',
              style: { fontSize: 11, color: T.faint },
              children: `${plural(p.skills_count, 'skill')} · ${plural(p.cron_count, 'routine')}`
            })
          ]
        }))
      ]),
      box({ key: 'panel', className: 'ht-panel' }, [
        jsxs('div', { key: 'form', style: { ...S.card, display: 'flex', flexDirection: 'column', gap: 12 } }, [
          jsxs('div', { key: 'r1', className: 'ht-pair' }, [
            jsx(Field, { key: 'a', label: 'Template id' }, jsx('input', {
              style: S.input, className: FIELD_CLASS, value: form.name, onChange: set('name'), placeholder: 'dex'
            })),
            jsx(Field, { key: 'b', label: 'Version' }, jsx('input', {
              style: S.input, className: FIELD_CLASS, value: form.version, onChange: set('version'), placeholder: '1.0.0'
            }))
          ]),
          jsx(Field, {
            key: 'r2', label: 'Name people see',
            hint: 'One line that says what this agent is for.'
          }, jsx('input', {
            style: S.input, className: FIELD_CLASS, value: form.title, onChange: set('title'),
            placeholder: 'Dex — CTO, runs the delivery work'
          })),
          jsx(Field, { key: 'r3', label: 'Description' }, jsx('textarea', {
            style: { ...S.input, minHeight: 56, resize: 'vertical' }, className: FIELD_CLASS,
            value: form.description, onChange: set('description'),
            placeholder: 'What this agent does, for whom, and what it does not do.'
          })),
          jsxs('div', { key: 'r4', className: 'ht-pair' }, [
            jsx(Field, { key: 'a', label: 'Author' }, jsx('input', {
              style: S.input, className: FIELD_CLASS, value: form.author, onChange: set('author'), placeholder: 'Your name'
            })),
            jsx(Field, { key: 'b', label: 'License' }, jsx('input', {
              style: S.input, className: FIELD_CLASS, value: form.license, onChange: set('license'), placeholder: 'MIT'
            }))
          ]),
          box({ key: 'opts', style: { marginTop: 2 } }, [
            box({ key: 'l', style: { ...S.label, marginBottom: 2 } }, 'What travels'),
            jsx(Toggle, {
              key: 'skills', label: 'Skills', checked: options.skills, onChange: toggle('skills'),
              hint: profile && profile.skills_count ? `${profile.skills_count} skill(s), credentials inside them left out` : 'No skills in this profile'
            }),
            jsx(Toggle, {
              key: 'cron', label: 'Routines', checked: options.cron, onChange: toggle('cron'),
              hint: 'Installed paused: whoever installs decides what runs'
            }),
            jsx(Toggle, {
              key: 'mcp', label: 'MCP servers', checked: options.mcp, onChange: toggle('mcp'),
              hint: 'Definitions only — env values become keys to reconnect'
            }),
            jsx(Toggle, {
              key: 'plugins', label: 'Plugin ids', checked: options.plugins, onChange: toggle('plugins'),
              hint: 'Never the plugin code, never your accounts'
            }),
            jsx(Toggle, {
              key: 'memories', label: 'Memories', checked: options.memories, onChange: toggle('memories'), warn: options.memories,
              hint: 'Off by default. Memories are readable by anyone who installs the template.'
            })
          ]),
          box({
            key: 'never',
            style: { fontSize: 11.5, color: T.faint, lineHeight: 1.55, borderTop: `1px solid ${T.border}`, paddingTop: 10 }
          }, 'Never packed: .env, auth.json, sessions, conversation history, cookies, browser profiles, logs, caches, workspace. Every text file is redacted with Hermes’ secret redactor before packing.'),
          jsxs('div', { key: 'actions', style: { display: 'flex', alignItems: 'center', gap: 10 } }, [
            jsx(Button, { key: 'go', primary: true, onClick: create, disabled: busy || !form.name }, busy ? 'Packing…' : 'Create template'),
            error ? jsx('span', { key: 'e', style: { fontSize: 12, color: T.bad } }, error) : null
          ])
        ]),
        result ? jsxs('div', {
          key: 'result',
          style: { ...S.card, borderColor: T.stroke, display: 'flex', flexDirection: 'column', gap: 10 }
        }, [
          jsxs('div', { key: 'h', style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 } }, [
            jsxs('div', { key: 't' }, [
              jsx('div', { key: 'a', style: { fontSize: 13, fontWeight: 600 } }, result.manifest.title),
              jsx('div', {
                key: 'b', style: { fontSize: 11.5, color: T.faint, marginTop: 2 },
                children: `v${result.manifest.version} · ${result.files} files · ${bytes(result.bytes)} · ${result.redactions} redaction(s)`
              })
            ]),
            jsxs('div', { key: 'b', style: { display: 'flex', gap: 8 } }, [
              jsx(Button, { key: 'd', primary: true, onClick: save }, 'Download'),
              jsx(Button, {
                key: 'c',
                onClick: async () => {
                  const ok = await copy(`hermes template install ${result.path}`)
                  setCopied(ok ? 'command' : '')
                  setTimeout(() => setCopied(''), 1600)
                }
              }, copied === 'command' ? 'Copied' : 'Copy command')
            ])
          ]),
          jsx('div', {
            key: 'chips',
            children: [
              result.manifest.contents.soul ? jsx(Chip, { key: 's', tone: 'ok' }, 'identity') : null,
              result.manifest.contents.skills.length ? jsx(Chip, { key: 'sk' }, plural(result.manifest.contents.skills.length, 'skill')) : null,
              result.manifest.contents.cron.length ? jsx(Chip, { key: 'c' }, plural(result.manifest.contents.cron.length, 'routine')) : null,
              result.manifest.contents.mcp.length ? jsx(Chip, { key: 'm' }, plural(result.manifest.contents.mcp.length, 'server')) : null,
              result.manifest.contents.plugins.length ? jsx(Chip, { key: 'p' }, plural(result.manifest.contents.plugins.length, 'plugin')) : null,
              result.manifest.contents.memories ? jsx(Chip, { key: 'mem', tone: 'warn' }, 'memories included') : null
            ].filter(Boolean)
          }),
          result.notes && result.notes.length
            ? jsx('div', { key: 'notes', style: { display: 'flex', flexDirection: 'column' } },
                result.notes.map((note, i) => jsx('div', { key: i, style: { fontSize: 11.5, color: T.dim, lineHeight: 1.5 } }, `· ${note}`)))
            : null,
          jsx(PublishPanel, { key: 'publish', templateId: result.id })
        ]) : null
      ])
    ]
  })
}

// ---------------------------------------------------------------- Install

function installLabel(preview, target) {
  if (!preview.exists || target !== preview.name) return 'Install'
  const u = preview.update
  if (u && u.newer) return `Update to v${u.to}`
  if (u && u.same) return 'Reinstall'
  return 'Replace'
}

function InstallTab() {
  const [source, setSource] = useState('')
  const [upload, setUpload] = useState(null)
  const [preview, setPreview] = useState(null)
  const [target, setTarget] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(null)
  const [dragging, setDragging] = useState(false)
  const fileInput = useRef(null)

  const reset = () => {
    setPreview(null)
    setDone(null)
    setError('')
    setTarget('')
  }

  const inspect = async (payload) => {
    setBusy(true)
    setError('')
    setDone(null)
    try {
      const data = await requestReady(() => rest('/preview', { method: 'POST', body: payload }))
      setPreview(data)
      setTarget(data.exists && !data.replaceable ? `${data.name}-copy` : data.name)
      tap('tap')
    } catch (e) {
      setPreview(null)
      setError(String((e && e.message) || e))
    } finally {
      setBusy(false)
    }
  }

  const onPick = async (file) => {
    if (!file) return
    try {
      const data = await readFileBase64(file)
      setUpload({ data_base64: data, filename: file.name })
      setSource(file.name)
      await inspect({ data_base64: data, filename: file.name })
    } catch (e) {
      setError(String((e && e.message) || e))
    }
  }

  const install = async () => {
    if (!preview || busy) return
    setBusy(true)
    setError('')
    try {
      const body = upload
        ? { data_base64: upload.data_base64, filename: upload.filename, name: target, force: true }
        : { source, name: target, force: true }
      const data = await requestReady(() => rest('/install', { method: 'POST', body }), 3)
      setDone(data)
      tap('success')
    } catch (e) {
      setError(String((e && e.message) || e))
    } finally {
      setBusy(false)
    }
  }

  const manifest = preview ? preview.manifest : null

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 760 },
    children: [
      jsxs('div', {
        key: 'drop',
        onDragOver: (e) => { e.preventDefault(); setDragging(true) },
        onDragLeave: () => setDragging(false),
        onDrop: (e) => {
          e.preventDefault()
          setDragging(false)
          const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]
          if (file) onPick(file)
        },
        style: {
          ...S.card, display: 'flex', flexDirection: 'column', gap: 10,
          borderStyle: dragging ? 'dashed' : 'solid',
          borderColor: dragging ? T.accent : T.border
        }
      }, [
        jsx('div', { key: 'l', style: S.label }, 'Install a template'),
        jsxs('div', { key: 'row', style: { display: 'flex', gap: 8 } }, [
          jsx('input', {
            key: 'i', style: { ...S.input, flex: 1 }, className: FIELD_CLASS,
            placeholder: 'https://… .htemplate, or /path/to/file.htemplate',
            value: source,
            onChange: (e) => { setSource(e.target.value); setUpload(null); setPreview(null) },
            onKeyDown: (e) => { if (e.key === 'Enter' && source) inspect({ source }) },
            onPaste: (e) => {
              // A pasted link is the whole gesture: no second click on Preview.
              const text = String((e.clipboardData && e.clipboardData.getData('text')) || '').trim()
              if (/^https?:\/\/\S+$/.test(text)) {
                e.preventDefault()
                setSource(text)
                setUpload(null)
                inspect({ source: text })
              }
            }
          }),
          jsx(Button, { key: 'b', onClick: () => inspect({ source }), disabled: busy || !source }, busy ? 'Checking…' : 'Preview')
        ]),
        jsxs('div', { key: 'or', style: { display: 'flex', alignItems: 'center', gap: 10 } }, [
          jsx('div', { key: 'l', style: { flex: 1, height: 1, background: T.border } }),
          jsx('span', { key: 't', style: { fontSize: 11, color: T.faint } }, 'or drop the file here'),
          jsx('div', { key: 'r', style: { flex: 1, height: 1, background: T.border } })
        ]),
        jsxs('div', { key: 'pick', style: { display: 'flex', gap: 8, alignItems: 'center' } }, [
          jsx(Button, { key: 'b', onClick: () => fileInput.current && fileInput.current.click() }, 'Choose a file…'),
          jsx('input', {
            key: 'f', ref: fileInput, type: 'file', accept: '.htemplate,.zip', style: { display: 'none' },
            onChange: (e) => onPick(e.target.files && e.target.files[0])
          }),
          jsx('span', { key: 'h', style: { fontSize: 11, color: T.faint } },
            'Preview first: nothing is written before you press Install.')
        ]),
        error ? jsx(Notice, { key: 'e', tone: 'bad' }, error) : null
      ]),

      preview ? jsxs('div', {
        key: 'preview',
        style: { ...S.card, display: 'flex', flexDirection: 'column', gap: 12 }
      }, [
        jsxs('div', { key: 'h', style: { display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 } }, [
          jsxs('div', { key: 't' }, [
            jsx('div', { key: 'a', style: { fontSize: 14, fontWeight: 600 } }, manifest.title),
            jsx('div', {
              key: 'b', style: { fontSize: 11.5, color: T.faint, marginTop: 2 },
              children: [
                `v${manifest.version}`,
                manifest.author ? ` · ${manifest.author}` : '',
                manifest.license ? ` · ${manifest.license}` : '',
                ` · ${bytes(preview.bytes)}`
              ].join('')
            })
          ]),
          jsx('div', { key: 'c', style: { fontSize: 11, color: T.faint } }, `${preview.files.length} files`)
        ]),
        manifest.description
          ? jsx('div', { key: 'd', style: { fontSize: 12.5, color: T.dim, lineHeight: 1.55 } }, manifest.description)
          : null,
        jsx('div', {
          key: 'chips',
          children: [
            manifest.contents.soul ? jsx(Chip, { key: 's', tone: 'ok' }, 'SOUL.md') : null,
            manifest.contents.skills.length ? jsx(Chip, { key: 'sk' }, plural(manifest.contents.skills.length, 'skill')) : null,
            manifest.contents.cron.length ? jsx(Chip, { key: 'c' }, plural(manifest.contents.cron.length, 'routine')) : null,
            manifest.contents.mcp.length ? jsx(Chip, { key: 'm' }, plural(manifest.contents.mcp.length, 'server')) : null,
            manifest.contents.plugins.length ? jsx(Chip, { key: 'p' }, plural(manifest.contents.plugins.length, 'plugin')) : null,
            manifest.contents.memories ? jsx(Chip, { key: 'mem', tone: 'warn' }, 'memories') : null
          ].filter(Boolean)
        }),
        manifest.contents.skills.length
          ? jsxs('div', { key: 'sk', style: { fontSize: 11.5, color: T.dim, lineHeight: 1.6 } }, [
              jsx('span', { key: 'l', style: { color: T.faint } }, 'Skills: '),
              manifest.contents.skills.join(', ')
            ])
          : null,
        manifest.contents.cron.length
          ? jsxs('div', { key: 'cr', style: { fontSize: 11.5, color: T.dim, lineHeight: 1.6 } }, [
              jsx('span', { key: 'l', style: { color: T.faint } }, 'Routines (paused): '),
              manifest.contents.cron.join(', ')
            ])
          : null,
        preview.env_requires.length
          ? jsxs('div', { key: 'env', style: { display: 'flex', flexDirection: 'column', gap: 3 } }, [
              jsx('div', { key: 'l', style: S.label }, 'Keys to reconnect'),
              ...preview.env_requires.map((item) => jsxs('div', {
                key: item.name,
                style: { fontSize: 11.5, color: item.set || item.in_env_file ? T.ok : T.dim }
              }, [
                jsx('span', { key: 'm' }, item.set || item.in_env_file ? '✓ ' : '· '),
                jsx('code', { key: 'n', style: { fontFamily: 'var(--ui-font-mono, monospace)' } }, item.name),
                jsx('span', { key: 'd', style: { color: T.faint } }, item.description ? ` — ${item.description}` : '')
              ]))
            ])
          : null,
        preview.warnings.length
          ? jsx('div', { key: 'w', style: { display: 'flex', flexDirection: 'column' } },
              preview.warnings.map((warning, i) => jsx(Notice, { key: i, tone: 'warn' }, warning)))
          : null,
        jsxs('div', { key: 'target', style: { display: 'flex', alignItems: 'flex-end', gap: 10 } }, [
          jsx('div', { key: 'f', style: { flex: 1 } }, jsx(Field, {
            label: 'Install as profile',
            hint: target && target === preview.name && preview.exists
              ? (preview.replaceable
                  ? 'Already installed from a template — Install updates it, your data stays.'
                  : 'That name is taken by a profile this installer did not create.')
              : `New profile named ${target || '…'} under profiles/`
          }, jsx('input', {
            style: S.input, className: FIELD_CLASS, value: target, onChange: (e) => setTarget(e.target.value)
          }))),
          jsx(Button, {
            key: 'go', primary: true, onClick: install,
            disabled: busy || !target
          }, busy ? 'Installing…' : installLabel(preview, target))
        ])
      ]) : null,

      done ? jsxs('div', {
        key: 'done',
        style: { ...S.card, borderColor: T.ok, display: 'flex', flexDirection: 'column', gap: 6 }
      }, [
        jsx('div', { key: 't', style: { fontSize: 13, fontWeight: 600, color: T.ok } },
          `${done.replaced ? 'Updated' : 'Installed'} “${done.profile}”`),
        jsx('div', { key: 'p', style: { fontSize: 11.5, color: T.faint } }, done.path),
        done.plugins_enabled.length
          ? jsx('div', { key: 'pl', style: { fontSize: 11.5, color: T.dim } }, `Plugins enabled: ${done.plugins_enabled.join(', ')}`)
          : null,
        done.plugins_missing.length
          ? jsx('div', { key: 'pm', style: { fontSize: 11.5, color: T.warn } }, `Not installed here: ${done.plugins_missing.join(', ')}`)
          : null,
        jsx('div', { key: 'h', style: { fontSize: 11.5, color: T.faint, marginTop: 2 } },
          `Start it with: hermes -p ${done.profile} chat — routines are paused, review them before enabling.`),
        (done.manifest.contents.cron || []).length
          ? jsx('div', { key: 'rev', style: { marginTop: 6 } }, jsx(Button, {
              onClick: () => { try { host.navigate('/cron') } catch (e) { /* older host */ } }
            }, `Review ${plural(done.manifest.contents.cron.length, 'routine')}`))
          : null,
        (done.next_steps || []).length
          ? jsxs('div', { key: 'ns', style: { display: 'flex', flexDirection: 'column', gap: 3, marginTop: 4 } }, [
              jsx('div', { key: 'l', style: { ...S.label } }, 'Connect your keys'),
              ...done.next_steps.map((step, i) => jsx('code', {
                key: i,
                style: {
                  fontFamily: 'var(--ui-font-mono, monospace)', fontSize: 11, color: T.dim,
                  background: T.code, border: `1px solid ${T.stroke}`, borderRadius: 5, padding: '3px 6px'
                }
              }, step))
            ])
          : null
      ]) : null
    ]
  })
}

// ---------------------------------------------------------------- Page

function TemplatesPage() {
  const [tab, setTab] = useState('share')
  const [profiles, setProfiles] = useState(null)
  const [error, setError] = useState('')

  const load = () => {
    setError('')
    requestReady(() => rest('/profiles'))
      .then((data) => setProfiles(data.profiles || []))
      .catch((e) => { setProfiles([]); setError(String((e && e.message) || e)) })
  }

  useEffect(() => { load() }, [])

  const tabButton = (id, label) => jsx('button', {
    key: id,
    type: 'button',
    onClick: () => { tap('tap'); setTab(id) },
    style: {
      appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer',
      fontFamily: 'inherit', fontSize: 12.5, padding: '4px 2px', fontWeight: 550,
      color: tab === id ? T.text : T.faint,
      borderBottom: `1.5px solid ${tab === id ? T.accent : 'transparent'}`
    },
    children: label
  })

  return jsxs('div', {
    className: 'ht-page',
    style: S.page,
    children: [
      jsxs('div', { key: 'h', style: S.header }, [
        jsxs('div', { key: 't' }, [
          jsx('div', { key: 'a', style: S.h1 }, 'Templates'),
          jsx('div', { key: 'b', style: S.sub }, 'Share a whole agent, or install one from a link.')
        ]),
        jsxs('div', { key: 'tabs', style: { display: 'flex', gap: 16 } }, [
          tabButton('share', 'Share'),
          tabButton('install', 'Install')
        ])
      ]),
      jsx('div', {
        key: 'b', style: S.body,
        children: error
          ? jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 520 } }, [
              jsx(Notice, { tone: 'bad' }, `Backend unreachable: ${error}`),
              jsx('div', {}, jsx(Button, { onClick: load }, 'Retry'))
            ])
          : profiles === null
            ? jsx('div', { style: { fontSize: 12.5, color: T.faint } }, 'Loading…')
            : tab === 'share'
              ? jsx(ShareTab, { profiles, onCreated: load })
              : jsx(InstallTab, {})
      })
    ]
  })
}

export default {
  id: 'hermes-templates',
  name: 'Agent Templates',
  register(ctx) {
    rest = ctx.rest
    osDoor = ctx.os || null
    injectFieldCss()

    ctx.register({
      id: 'page',
      area: ROUTES,
      data: { path: '/templates' },
      render: () => jsx(TemplatesPage, {})
    })

    ctx.register({
      id: 'nav',
      area: SIDEBAR_NAV,
      order: 40,
      data: { path: '/templates', label: 'Templates', codicon: 'versions' }
    })
  }
}