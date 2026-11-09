// hermes-templates — Desktop half: a Templates page with a Share tab and an Install tab.
//
// Everything the page shows comes from this package's own backend
// (dashboard/plugin_api.py) through ctx.rest. The bundle itself never leaves the
// machine unless the person clicks Download, and the page cannot install
// anything the preview did not show first.
import { host, haptic, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

let rest = null

const ROUTES = ROUTES_AREA || 'routes'
const SIDEBAR_NAV = SIDEBAR_NAV_AREA || 'sidebar.nav'

const T = {
  text: 'var(--ui-text-primary)',
  dim: 'var(--ui-text-secondary)',
  faint: 'var(--ui-text-tertiary)',
  border: 'var(--ui-border)',
  stroke: 'var(--ui-stroke-secondary)',
  card: 'var(--ui-bg-card)',
  raised: 'var(--ui-bg-elevated)',
  input: 'var(--ui-bg-input)',
  accent: 'var(--ui-accent)',
  ok: 'var(--ui-success)',
  warn: 'var(--ui-warning)',
  bad: 'var(--ui-danger)'
}

const el = (tag, props, children) => jsx(tag, { ...props, children })
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
  label: { fontSize: 11, fontWeight: 500, color: T.faint, textTransform: 'uppercase', letterSpacing: '0.04em' },
  input: {
    width: '100%', boxSizing: 'border-box', background: T.input, color: T.text,
    border: `1px solid ${T.stroke}`, borderRadius: 7, padding: '7px 9px', fontSize: 13,
    fontFamily: 'inherit', outline: 'none'
  },
  btn: (primary, disabled) => ({
    appearance: 'none', border: `1px solid ${primary ? 'transparent' : T.stroke}`,
    background: primary ? T.accent : 'transparent',
    color: primary ? 'var(--ui-bg-primary)' : T.text,
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
    await navigator.clipboard.writeText(text)
    haptic('tap')
    return true
  } catch (e) {
    return false
  }
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
      name: profile.id,
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
        body: JSON.stringify({
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
      haptic('success')
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
      const payload = await rest(`/download/${result.id}`, { method: 'POST', body: JSON.stringify({}) })
      downloadBlob(payload.filename, payload.data_base64)
    } catch (e) {
      setError(String((e && e.message) || e))
    }
  }

  if (!profiles.length) {
    return box({ style: { ...S.card, color: T.faint, fontSize: 13 } }, 'No profile to share yet — create one in Hermes first.')
  }

  return jsxs('div', {
    style: { display: 'grid', gridTemplateColumns: 'minmax(180px, 240px) 1fr', gap: 18, alignItems: 'start' },
    children: [
      box({ key: 'list', style: { display: 'flex', flexDirection: 'column', gap: 3 } }, [
        box({ key: 'l', style: { ...S.label, marginBottom: 6 } }, 'Your agents'),
        ...profiles.map((p) => jsxs('button', {
          key: p.id,
          type: 'button',
          onClick: () => { haptic('tap'); setSelected(p.id) },
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
              children: `${p.skills_count} skills · ${p.cron_count} routines`
            })
          ]
        }))
      ]),
      box({ key: 'panel', style: { display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 } }, [
        jsxs('div', { key: 'form', style: { ...S.card, display: 'flex', flexDirection: 'column', gap: 12 } }, [
          jsxs('div', { key: 'r1', style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 } }, [
            jsx(Field, { key: 'a', label: 'Template id' }, jsx('input', {
              style: S.input, value: form.name, onChange: set('name'), placeholder: 'dex'
            })),
            jsx(Field, { key: 'b', label: 'Version' }, jsx('input', {
              style: S.input, value: form.version, onChange: set('version'), placeholder: '1.0.0'
            }))
          ]),
          jsx(Field, {
            key: 'r2', label: 'Name people see',
            hint: 'One line that says what this agent is for.'
          }, jsx('input', {
            style: S.input, value: form.title, onChange: set('title'),
            placeholder: 'Dex — CTO qui orchestre les livraisons'
          })),
          jsx(Field, { key: 'r3', label: 'Description' }, jsx('textarea', {
            style: { ...S.input, minHeight: 56, resize: 'vertical' },
            value: form.description, onChange: set('description'),
            placeholder: 'Ce que l’agent fait, pour qui, et ce qu’il ne fait pas.'
          })),
          jsxs('div', { key: 'r4', style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 } }, [
            jsx(Field, { key: 'a', label: 'Author' }, jsx('input', {
              style: S.input, value: form.author, onChange: set('author'), placeholder: 'Your name'
            })),
            jsx(Field, { key: 'b', label: 'License' }, jsx('input', {
              style: S.input, value: form.license, onChange: set('license'), placeholder: 'MIT'
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
              result.manifest.contents.skills.length ? jsx(Chip, { key: 'sk' }, `${result.manifest.contents.skills.length} skills`) : null,
              result.manifest.contents.cron.length ? jsx(Chip, { key: 'c' }, `${result.manifest.contents.cron.length} routines`) : null,
              result.manifest.contents.mcp.length ? jsx(Chip, { key: 'm' }, `${result.manifest.contents.mcp.length} mcp`) : null,
              result.manifest.contents.plugins.length ? jsx(Chip, { key: 'p' }, `${result.manifest.contents.plugins.length} plugins`) : null,
              result.manifest.contents.memories ? jsx(Chip, { key: 'mem', tone: 'warn' }, 'memories included') : null
            ].filter(Boolean)
          }),
          result.notes && result.notes.length
            ? jsx('div', { key: 'notes', style: { display: 'flex', flexDirection: 'column' } },
                result.notes.map((note, i) => jsx('div', { key: i, style: { fontSize: 11.5, color: T.dim, lineHeight: 1.5 } }, `· ${note}`)))
            : null,
          jsx('div', { key: 'share', style: { fontSize: 11.5, color: T.faint, lineHeight: 1.55 } },
            'To share it as a link: upload the downloaded .htemplate anywhere that serves HTTPS (a gist, your site, a bucket) and send that URL. Whoever opens it pastes the link in Install.')
        ]) : null
      ])
    ]
  })
}

// ---------------------------------------------------------------- Install

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
      const data = await requestReady(() => rest('/preview', { method: 'POST', body: JSON.stringify(payload) }))
      setPreview(data)
      setTarget(data.name)
      haptic('tap')
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
      const data = await requestReady(() => rest('/install', { method: 'POST', body: JSON.stringify(body) }), 3)
      setDone(data)
      haptic('success')
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
            key: 'i', style: { ...S.input, flex: 1 },
            placeholder: 'https://… .htemplate, or /path/to/file.htemplate',
            value: source,
            onChange: (e) => { setSource(e.target.value); setUpload(null); setPreview(null) },
            onKeyDown: (e) => { if (e.key === 'Enter' && source) inspect({ source }) }
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
            manifest.contents.skills.length ? jsx(Chip, { key: 'sk' }, `${manifest.contents.skills.length} skills`) : null,
            manifest.contents.cron.length ? jsx(Chip, { key: 'c' }, `${manifest.contents.cron.length} routines`) : null,
            manifest.contents.mcp.length ? jsx(Chip, { key: 'm' }, `${manifest.contents.mcp.length} mcp`) : null,
            manifest.contents.plugins.length ? jsx(Chip, { key: 'p' }, `${manifest.contents.plugins.length} plugins`) : null,
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
            hint: preview.exists
              ? (preview.replaceable ? 'Already installed from a template — Install updates it, your data stays.' : 'That name is taken by a profile this installer did not create.')
              : `New profile at ${preview.target_path}`
          }, jsx('input', {
            style: S.input, value: target, onChange: (e) => setTarget(e.target.value)
          }))),
          jsx(Button, {
            key: 'go', primary: true, onClick: install,
            disabled: busy || !target || (preview.exists && !preview.replaceable)
          }, busy ? 'Installing…' : preview.exists ? 'Update' : 'Install')
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
          `Start it with: hermes -p ${done.profile} chat — routines are paused, review them before enabling.`)
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
    onClick: () => { haptic('tap'); setTab(id) },
    style: {
      appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer',
      fontFamily: 'inherit', fontSize: 12.5, padding: '4px 2px', fontWeight: 550,
      color: tab === id ? T.text : T.faint,
      borderBottom: `1.5px solid ${tab === id ? T.accent : 'transparent'}`
    },
    children: label
  })

  return jsxs('div', {
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
