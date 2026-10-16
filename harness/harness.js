// Renders desktop/plugin.js inside a Hermes-like window. ctx.rest goes to the real backend (api.py)
// through harness/serve.py. URL parameters:
//   view     fit | answer | crew | lab           (default fit)
//   id       a crew slug (view=crew)
//   job      a common job slug: fills every Fit Test answer from it
//   set      extra answers, as key:value,key:value (true/false for Yes/No)
//   lab      a sentence for Bot Lab (view=lab)
//   keep     name=<Name> renames the Bot Lab plan before the click
//   theme    dark | light                        (default dark)
//   state    offline | loading                   (backend not mounted, or the first answer never comes)
//   click    text of a button to click after load (repeat with click2, click3)
//   tick     the start of a checkbox's label: ticked after click and before click2
//   seed     taken | mybots | hermes-bots, or several joined by commas (a scene in the sandbox: profiles you already have, things Cabinet set up,
//                                                 or Bots Hermes's own catalog added: planner, and research-analyst)
//   live     1                                   (pretend botcabinet.com publishes a newer catalog)
//   handover 1                                   (pretend this Hermes ships its own Bots catalog: the gateway answers bots.catalog)
//   edit     field=value;field=value             (type into inputs by aria-label before the click)
import { createRoot } from 'react-dom/client'
import { jsx, jsxs } from 'react/jsx-runtime'

const params = new URLSearchParams(location.search)
const view = params.get('view') || 'start'
document.documentElement.dataset.theme = params.get('theme') === 'light' ? 'light' : 'dark'
window.__hostLog = []
if (params.get('handover')) window.__harness = { ...(window.__harness || {}), hermesBots: true }
const pause = ms => new Promise(r => setTimeout(r, ms))

async function rest(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase()
  if (params.get('state') === 'offline') { await pause(40); throw new Error('404: {"detail":"Not Found"}') }
  if (params.get('state') === 'loading' && seeded) return new Promise(() => {})
  const init = { method, headers: { 'content-type': 'application/json' } }
  if (options.body !== undefined) init.body = JSON.stringify(options.body)  // what Hermes's pluginRest does
  const res = await fetch(`/api/${path}`, init)
  const text = await res.text()
  if (!res.ok) throw new Error(`${res.status}: ${text}`)
  return JSON.parse(text)
}

const store = new Map()
async function seed() {
  const form = await rest('fit')
  let answers = {}
  if (params.get('job')) answers = { ...form.commonJobs.find(j => j.slug === params.get('job')).answers }
  for (const pair of (params.get('set') || '').split(',').filter(Boolean)) {
    const [k, ...rest] = pair.split(':')
    const v = rest.join(':')
    answers[k] = v === 'true' ? true : v === 'false' ? false : v
  }
  if (Object.keys(answers).length) store.set('answers', answers)
  if (params.get('lab')) store.set('lab-job', params.get('lab'))
}
async function harnessPost(path, body) {
  await fetch(`/__h/${path}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}) })
}
let seeded = false
if (params.get('state') !== 'offline') {
  // The backend is a sandbox: start every screen from an empty one, then set the scene.
  await harnessPost('reset')
  if (params.get('live')) await harnessPost('live', { on: true })
  if (params.get('handover')) await harnessPost('handover', { on: true })
  for (const what of (params.get('seed') || '').split(',').filter(Boolean)) await harnessPost('seed', { what })
  await seed()
}
seeded = true
try { await document.fonts.load('16px codicon') } catch (e) { /* icons may be missing */ }

const contributions = []
const ctx = {
  rest, registerMany(list) { contributions.push(...list) }, register(i) { contributions.push(i) },
  onEvent() { return () => {} }, onDispose() {}, setTimeout, setInterval,
  storage: { get: (k, f) => (store.has(k) ? store.get(k) : f), set: (k, v) => store.set(k, v), remove: k => store.delete(k) },
  os: { async openExternal(u) { window.__hostLog.push(['openExternal', u]); return true }, async writeClipboard() { return true } },
}
const mod = await import('../desktop/plugin.js')
mod.default.register(ctx)
mod.openView({ name: view, id: params.get('id') || '', q: params.get('q') || '', status: params.get('status') || '', category: params.get('category') || '' })
const page = contributions.find(c => c.area === 'routes')
const nav = contributions.filter(c => c.area === 'sidebar.nav')
const CORE = [['New session', 'robot'], ['Capabilities', 'extensions'], ['Messaging', 'comment'], ['Kanban', 'layout'], ['Scheduled jobs', 'watch']]

function Shell() {
  return jsxs('div', { className: 'hb-window', children: [
    jsx('div', { className: 'hb-titlebar', children: jsxs('div', { className: 'hb-lights', children: [jsx('span', {}), jsx('span', {}), jsx('span', {})] }) }),
    jsxs('div', { className: 'hb-body', children: [
      jsxs('nav', { className: 'hb-sidebar', children: [
        ...CORE.map(([l, i]) => jsxs('div', { className: 'hb-nav', children: [jsx('i', { className: `codicon codicon-${i}` }), l] }, l)),
        ...nav.map(n => jsxs('div', { className: 'hb-nav hb-nav-active', children: [jsx('i', { className: `codicon codicon-${n.data.codicon}` }), n.data.label] }, n.id)),
        jsx('div', { className: 'hb-sep' }), jsx('div', { className: 'hb-side-label', children: 'Bots' }),
        ...['Researcher', 'Planner', 'Writer'].map(t => jsxs('div', { className: 'hb-nav', children: [jsx('i', { className: 'codicon codicon-account' }), t] }, t)),
      ] }),
      jsx('main', { className: 'hb-main', children: page.render() }),
    ] }),
    jsx('div', { id: 'hb-toasts' }),
  ] })
}
createRoot(document.getElementById('root')).render(jsx(Shell, {}))

async function clickText(text) {
  const deadline = Date.now() + 25000
  while (Date.now() < deadline) {
    const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === text && !x.disabled)
    if (b) { b.click(); return true }
    await pause(50)
  }
  return false
}
async function setField(label, value) {
  const el = document.querySelector(`textarea[aria-label="${label}"],input[aria-label="${label}"]`)
  if (!el) return
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}
await pause(300)
const keep = params.get('keep') || ''
if (keep.startsWith('name=')) { await pause(400); await setField('Name', keep.slice(5)); await pause(150) }
if (keep) await pause(700) // let the plan check catch up with the new name
for (const pair of (params.get('edit') || '').split(';').filter(Boolean)) {
  const [label, ...rest] = pair.split('=')
  await pause(500)
  await setField(label, rest.join('='))
  await pause(700)
}
async function tick(text) {
  const deadline = Date.now() + 25000
  while (Date.now() < deadline) {
    const label = [...document.querySelectorAll('label')].find(x => x.textContent.trim().startsWith(text))
    const box = label && (label.querySelector('input[type=checkbox],button[role=checkbox],[role=checkbox]') || (label.htmlFor && document.getElementById(label.htmlFor)))
    if (box) { if (box.getAttribute('aria-checked') !== 'true' && !box.checked) box.click(); return true }
    await pause(50)
  }
  return false
}
for (const key of ['click', 'tick', 'click2', 'click3']) {
  if (!params.get(key)) continue
  if (key === 'tick') await tick(params.get(key))
  else await clickText(params.get(key))
  await pause(700)
}
await pause(700)
try { await document.fonts.load('16px codicon') } catch (e) { /* icons may be missing */ }
document.documentElement.dataset.ready = '1'
