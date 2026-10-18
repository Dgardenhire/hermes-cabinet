// Cabinet, desktop half. SOURCE: desktop/plugin.js is built from this file, site-data.js and the shared
// hermes-ui-kit by `node ../hermes-ui-kit/scripts/build.mjs .` (esbuild). Edit here, then build.
//
// The built file is one ES module that imports only '@hermes/plugin-sdk', 'react' and
// 'react/jsx-runtime' (plugin-catalog rule 8). No JSX: jsx() calls, like the app's plugins.
//
// Two sources of truth, never mixed:
//  - botcabinet.com itself. The page starts from site-data.js (every Bot, its painted portrait, its test
//    status, the Crew Kits, the Test Records and the Inspection Desk's labels, copied by
//    tools/make_site_data.py at one site commit; a desktop plugin loads nothing over the network, so the
//    art is embedded). The Python backend then fetches the site's current catalog (or serves its last
//    good copy) and GET /catalog hands it over: its text, Bots and Crew Kits replace the embedded ones,
//    and the embedded portraits and test records stay attached by slug.
//  - the Python backend, through ctx.rest: the Fit Test's answer, Bot Lab's plan, and the review
//    screens for adding a Bot or setting up a crew (POST /bots/{slug}/plan, POST /crews/{slug}/setup).
//    The page only renders, keeps your unfinished answers, and opens chats with a message in the box
//    for you to send.
//
// Adding is three steps and the page owns only the middle one. The backend plans the profile and checks
// it; the page sends that exact request to Hermes's gateway (profiles.create, the same call New Agent
// uses, so the new bot gets your sign-in and model); the backend then checks what Hermes made and
// records it, so My Bots can undo it. Nothing is made until you press the button on a review screen.
//
// Sections mirror the site, plus one: Start Here, Fit Test, The Cabinet, Bot Lab, Crew Kits, My Bots,
// Test Records. Styling: the kit's tokens plus Cabinet's brass, all mixed from the app's theme variables.

import { useEffect, useRef, useState } from 'react'
import { Fragment, jsx, jsxs } from 'react/jsx-runtime'
import { Button, Codicon, Input, SegmentedControl, Textarea, atom, host, useValue } from '@hermes/plugin-sdk'
import {
  Back as KitBack, Card, Details, Empty, Facts, Field, KeyHint, Meter, NotConnected, Failure, Page as KitPage, Pill,
  SkeletonPage, Stepper, describeError, installStyles, isTyping, modKey, registerPage, submitOnModEnter, useFocusOnMount,
  useKeys, usePolled,
} from 'hermes-ui-kit'
import { IMAGES, SITE } from './site-data.js'

export { describeError }

const PLUGIN_ID = 'bot-cabinet-v2'
const ROUTE = '/cabinet'

let api = null
const $view = atom({ name: 'start' })
const $answers = atom({})
const $lab = atom({ job: '', plan: null, kept: {} })
const $cabinet = atom({ q: '', status: 'all', category: 'all' })
// Where the Bots and Crew Kits came from, which of Cabinet's Bots Hermes's own catalog already added (slug -> profile),
// and what the backend can do.
const $catalog = atom({ status: null, handover: null, hermesAdded: {}, installer: null, v: 0, refreshing: false })
// Hermes's own Bots catalog (NousResearch/hermes-agent#119260), as this Hermes's gateway reports it through bots.catalog.
// names: catalog name -> its title. Not available until the gateway answers; any failure means there is none.
const $hermesBots = atom({ checked: false, available: false, names: {} })

/* ------------------------------------------------------------------ site data -- */

// The Bots and Crew Kits in use. They start as the embedded copy and are rebuilt by applyCatalog() when the
// backend reports a newer catalog; every component reads them at render time.
let BOTS = SITE.bots
let BOT = {}
let BOT_BY_NAME = {}
let CREWS = SITE.crews
let CREW = {}
let TESTED = []
const CREWS_OF = slug => CREWS.filter(c => c.members.some(m => m.slug === slug))
const SNAPSHOT = SITE.siteCommit.split(' ')[1] || ''
const CATEGORY_LABEL = Object.fromEntries(SITE.categories.map(c => [c.key, c.label]))
// The crew test results in plain words. The site's own labels ("Failed acceptance", "Correction prepared; not
// tested") are its internal terms; inside Hermes a crew test either passed, failed, or hasn't been run.
const CREW_TEST_LABEL = { 'import-test-passed': 'Passed', passed: 'Passed', 'not-tested': 'Not tested', 'failed-acceptance': 'Failed',
  'correction-prepared-not-tested': 'Not tested', 'not-recorded': 'Not tested' }
const crewPassed = c => c.testStatus === 'import-test-passed' || c.testStatus === 'passed'
const crewFailed = c => c.testStatus === 'failed-acceptance'
// The five answers the Fit Test can give, named the same way everywhere (fit.KINDS in the backend).
const KIND_TEXT = {
  assignment: { title: 'Ask Hermes once', meaning: 'A single request in a chat' },
  skill: { title: 'Save it as a skill', meaning: 'Steps Hermes follows each time you ask' },
  routine: { title: 'Run it on a schedule', meaning: 'A Bot’s job that runs at a set time' },
  bot: { title: 'One Bot', meaning: 'One specialist with its own ongoing chat' },
  crew: { title: 'A crew', meaning: 'Several Bots, each with its own job, passing work in order' },
}

// The site's deck ("choose where to run it") is written for the website. Inside Hermes the Bot runs here.
const HERO_DECK = 'Add a Bot to Hermes and try it on one real task. Each Bot page shows what has been tested and what has not'

const stripPeriod = text => String(text || '').replace(/[.\s]+$/, '')
const titleWord = token => String(token || '').replace(/-/g, ' ').replace(/^./, c => c.toUpperCase())

/** The catalog's Bot, with the embedded portrait and test record attached when the embedded copy has the same Bot. */
function mergeBot(live) {
  const base = SITE.bots.find(b => b.slug === live.slug) || null
  const sameTest = base && live.test && base.test.status === live.test.status
  const test = sameTest ? base.test : {
    status: live.test ? live.test.status : 'not-tested', kind: 'import-only', tested: [], notTested: [],
    line: live.test && live.test.line ? live.test.line : 'No recorded task run yet',
  }
  return {
    slug: live.slug, name: live.name, title: live.title, category: live.category, categoryLabel: CATEGORY_LABEL[live.category] || titleWord(live.category),
    summary: stripPeriod(live.summary), audience: stripPeriod(live.audience), outcome: stripPeriod(live.outcome), inputs: live.inputs, outputs: live.outputs,
    examples: live.examples, firstMission: live.firstMission, approvals: live.approvals, offLimits: live.offLimits, worksWith: live.worksWith,
    portrait: base ? base.portrait : null, credits: live.credits, learnMore: base ? base.learnMore : [], href: live.href,
    skills: live.skills || [], routine: live.routine || null, riskLevel: live.riskLevel || '', test,
  }
}

function whoFor(owner, members) {
  // Same reading as the backend (installer.handoff_steps): bots in the order the step names them; a person,
  // owner, learner or hiring manager is you.
  const lower = String(owner || '').toLowerCase()
  const who = members.filter(m => lower.includes(m.name.toLowerCase()))
    .sort((a, b) => lower.indexOf(a.name.toLowerCase()) - lower.indexOf(b.name.toLowerCase()))
    .map(m => ({ bot: m.slug, label: m.name }))
  const person = /\b(hiring manager|person|you|owner|learner)\b/i.exec(owner)
  if (person) who.push({ you: true, label: /hiring manager|learner/i.test(person[1]) ? titleWord(person[1].toLowerCase()) : 'Person' })
  return who.length ? who : [{ label: owner }]
}

function mergeCrew(live) {
  const base = SITE.crews.find(c => c.slug === live.slug) || null
  return {
    slug: live.slug, name: live.name, summary: stripPeriod(live.summary), members: live.members,
    steps: live.handoffOrder.map((s, i) => ({ n: i + 1, step: s.timing, action: stripPeriod(s.action), owner: s.owner, who: whoFor(s.owner, live.members) })),
    approvals: live.approvalActions, offLimits: live.prohibitedActions, testStatus: live.testStatus,
    testLabel: CREW_TEST_LABEL[live.testStatus] || titleWord(live.testStatus), testDate: base ? base.testDate : null, testHref: base ? base.testHref : null,
    permissions: base ? base.permissions : null, learnMore: base ? base.learnMore : [], href: live.href,
  }
}

function buildCatalog(live) {
  BOTS = live ? live.bots.map(mergeBot) : SITE.bots
  CREWS = live ? live.crews.map(mergeCrew) : SITE.crews
  BOT = Object.fromEntries(BOTS.map(b => [b.slug, b]))
  BOT_BY_NAME = Object.fromEntries(BOTS.map(b => [b.name.toLowerCase(), b]))
  CREW = Object.fromEntries(CREWS.map(c => [c.slug, c]))
  TESTED = BOTS.filter(b => b.test.status === 'tested')
}
buildCatalog(null)

function portraitOf(bot) {
  return bot ? IMAGES[bot.portrait] : null
}

function botNamed(name) {
  return BOT_BY_NAME[String(name || '').toLowerCase()] || null
}

function shortDate(iso) {
  if (!iso) return ''
  const [y, m, d] = iso.split('-').map(Number)
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${months[m - 1]} ${d}, ${y}`
}

// The day a timestamp falls on where the person is: a bot added on an Oklahoma evening read as the next day when the
// UTC date was cut from the ISO string.
function localDay(stamp) {
  const when = new Date(stamp || '')
  if (Number.isNaN(when.getTime())) return String(stamp || '').slice(0, 10)
  const pad = (n) => String(n).padStart(2, '0')
  return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}`
}

function siteAddress(href) {
  return String(href || '').replace(/^https?:\/\//, '').replace(/\/$/, '')
}

/* ------------------------------------------------------------------ plumbing -- */

function rest(path, options) {
  if (!api) return Promise.reject(new Error('Cabinet is still starting up.'))
  return api.rest(path, options || {})
}

function post(path, body) {
  // Hermes encodes the body itself (pluginRest -> hermesApi), so pass the object as it is.
  return rest(path, { method: 'POST', body })
}

/* ------------------------------------------------------------------ the live catalog -- */

const REFRESH_AFTER_MS = 30 * 60 * 1000
let lastRefresh = 0

/** The Bots catalog names the backend read from Hermes on disk (hermes_cli.bot_catalog), as { name: name }, or null. */
function backendLane(view) {
  const h = view && view.handover
  return h && h.available && Array.isArray(h.names) && h.names.length ? Object.fromEntries(h.names.map(n => [n, n])) : null
}

function applyCatalog(view) {
  buildCatalog(view)
  // The gateway probe can be slow or missing; what the backend read from Hermes itself counts too, so a carried Bot is
  // handed over even before the probe answers.
  const lane = backendLane(view)
  const hb = $hermesBots.get()
  if (lane && !hb.available) $hermesBots.set({ checked: true, available: true, names: { ...lane, ...hb.names } })
  const prev = $catalog.get()
  $catalog.set({ ...prev, status: view.status, handover: view.handover, hermesAdded: view.hermesAdded || {}, installer: view.installer,
    v: prev.v + 1, refreshing: false })
}

/* ------------------------------------------------------------------ Hermes's own Bots catalog -- */

// Capabilities -> Bots. The SDK has no door for hermes:// deep links (hermes://bot/install?catalog=<name> arrives only
// from the operating system), so the hand-over opens the tab and names the Bot to pick there.
const HERMES_BOTS_ROUTE = '/capabilities?tab=bots'
const PROBE_TIMEOUT_MS = 8000
const PROBE_RETRY_MS = 60 * 1000
let probedAt = 0
let probeOk = false

/**
 * Ask the gateway for its Bots catalog. An answer with an entries or removed list means the catalog is there; an
 * error, a timeout or any other shape means it isn't. A yes is kept for half an hour; a no is asked again after a
 * minute, since the gateway may still have been connecting. Refresh asks again straight away.
 */
async function probeHermesBots(force) {
  const age = Date.now() - probedAt
  if (!force && probedAt && age < (probeOk ? REFRESH_AFTER_MS : PROBE_RETRY_MS)) return
  probedAt = Date.now()
  let next = { checked: true, available: false, names: {} }
  try {
    const out = await host.request('bots.catalog', {}, PROBE_TIMEOUT_MS)
    if (out && typeof out === 'object' && (Array.isArray(out.entries) || Array.isArray(out.removed))) {
      const names = {}
      for (const e of out.entries || []) {
        if (e && typeof e.name === 'string' && e.name) names[e.name] = typeof e.title === 'string' && e.title ? e.title : e.name
      }
      next = { checked: true, available: true, names }
    }
  } catch (e) {
    // No bots.catalog on this gateway: fall back to what the backend read from Hermes below.
  }
  if (!next.available) {
    const lane = backendLane({ handover: $catalog.get().handover })
    if (lane) next = { checked: true, available: true, names: lane }
  }
  probeOk = next.available
  $hermesBots.set(next)
}

/** The title Hermes's catalog lists this Bot under (matched by slug), or null when the catalog doesn't carry it. */
function carriedBy(hb, bot) {
  return bot && hb.available && Object.hasOwn(hb.names, bot.slug) ? hb.names[bot.slug] : null
}

/** The profile Hermes's catalog already added for this Bot, or null. Read from disk by the backend. */
function addedBy(c, bot) {
  return bot && c.hermesAdded && typeof c.hermesAdded[bot.slug] === 'string' ? c.hermesAdded[bot.slug] : null
}

function openHermesBots(bot, title) {
  try {
    host.navigate(HERMES_BOTS_ROUTE)
    if (bot) notify('info', `Pick ${title || bot.name} in Hermes Bots and add it there`)
  } catch (e) {
    notify('error', 'The app couldn’t open Hermes Bots. Open Capabilities, then the Bots tab.')
  }
}

/**
 * Show the catalog the backend has now (its saved copy), then ask botcabinet.com for a newer one: once when
 * the page opens, again after half an hour away, and whenever you press Refresh. A failure keeps what's on
 * screen; the status line says why the saved copy is showing.
 */
async function loadCatalog(force) {
  probeHermesBots(force)
  try {
    applyCatalog(await rest('catalog'))
  } catch (e) {
    return // the backend isn't mounted: the embedded copy stays
  }
  if (!force && Date.now() - lastRefresh < REFRESH_AFTER_MS) return
  lastRefresh = Date.now()
  $catalog.set({ ...$catalog.get(), refreshing: true })
  try {
    applyCatalog(await rest('catalog/refresh', { method: 'POST' }))
  } catch (e) {
    $catalog.set({ ...$catalog.get(), refreshing: false })
  }
}

function remember(key, value) {
  try { api && api.storage && api.storage.set(key, value) } catch (e) { /* not saved; fine */ }
}

function recall(key, fallback) {
  try { return api && api.storage ? api.storage.get(key, fallback) : fallback } catch (e) { return fallback }
}

const SECTION_OF = { start: 'start', fit: 'fit', answer: 'fit', cabinet: 'cabinet', bot: 'cabinet', add: 'cabinet', lab: 'lab', crews: 'crews', crew: 'crews',
  setup: 'crews', mybots: 'mybots', records: 'records' }

function go(view) {
  $view.set(view)
  // Review screens aren't remembered: reopening Cabinet never lands you in the middle of an add.
  if (['start', 'fit', 'cabinet', 'lab', 'crews', 'mybots', 'records', 'bot', 'crew'].includes(view.name)) remember('view', { name: view.name, id: view.id })
}

/** For the screenshot harness: open a screen directly, with optional Cabinet filters. */
export function openView(view) {
  if (view && (view.q || view.status || view.category)) {
    $cabinet.set({ q: view.q || '', status: view.status || 'all', category: view.category || 'all' })
  }
  go({ name: view.name, id: view.id })
}

function setAnswers(next) {
  $answers.set(next)
  remember('answers', next)
}

function notify(kind, message) {
  try { host.notify({ kind, message }) } catch (e) { /* no toast; the page still updates */ }
}

/** Open an address in the browser. Resolves false when the app couldn't, so the caller can show it. */
async function openLink(href) {
  try {
    const ok = await (api && api.os ? api.os.openExternal(href) : false)
    return ok !== false
  } catch (e) {
    return false
  }
}

async function copyText(text, done) {
  try {
    await api.os.writeClipboard(text)
    notify('success', done || 'Copied to your clipboard')
    return true
  } catch (e) {
    notify('error', 'Couldn’t copy that.')
    return false
  }
}

const pause = ms => new Promise(r => setTimeout(r, ms))

/** A new chat with one of your bots, opened on its own profile: the link from My Bots into the Hermes bot. */
function openBotChat(profile) {
  try { host.newChat(profile) } catch (e) { notify('error', 'The app couldn’t open a chat with it. Find it in the Bots tab.') }
}

/**
 * Open a fresh chat and send `text` in it, so Hermes starts on the job straight away. The new chat's composer
 * mounts a moment after newChat(), so submit is tried every 125 ms for up to 3 seconds. Only when the app has no
 * composer door, or no composer ever takes the text, does it go to the clipboard instead.
 */
async function sendInNewChat(text) {
  try { host.newChat() } catch (e) { /* no new chat: the clipboard below still works */ }
  const composer = host.composer
  if (composer && typeof composer.submit === 'function') {
    for (let i = 0; i < 24; i++) {
      await pause(125)
      try {
        if (await composer.submit('new', text)) return true
      } catch (e) { break }
    }
  }
  try { await api.os.writeClipboard(text) } catch (e) { /* nothing else to try */ }
  notify('info', 'A new chat is open and the message is copied. Paste it and press Enter.')
  return false
}

// Hermes seeds a new profile's built-in files in a separate process, which can take longer than the gateway's
// 30-second default. A request that gives up early would report a failure for a bot Hermes then finishes making.
const CREATE_TIMEOUT_MS = 120000

/** Send the reviewed profiles.create request to Hermes, exactly as the review screen showed it. */
async function createProfile(request) {
  return host.request(request.method, request.params, CREATE_TIMEOUT_MS)
}

/** Hermes refused because a profile of that name is already there: nothing of Cabinet's was made. */
function alreadyExists(e) {
  return /already exists/i.test(String((e && e.message) || e || ''))
}

/**
 * Make one bot and record it. If the request fails, Cabinet still asks its backend to record the plan: a reply can
 * be lost while Hermes finishes. The backend then records the profile only if it is new since the plan and carries
 * exactly the planned instructions, and changes nothing in it (created: false). When Hermes says the profile already
 * exists, nothing was made for this plan and nothing is recorded.
 */
async function createAndRecord(request, planId) {
  let failure = null
  try {
    await createProfile(request)
  } catch (e) {
    failure = e
  }
  if (failure && alreadyExists(failure)) throw failure
  try {
    const out = await post('install/confirm', { planId, created: !failure })
    return { out, recorded: true, late: Boolean(failure) }
  } catch (e) {
    if (failure) throw failure // Hermes refused and nothing was made: show Hermes's reason
    return { out: null, recorded: false, error: describeError(e) } // made, but not recorded yet: My Bots picks it up
  }
}

/** A backend read, cached by path: the last answer stays on screen while it reloads. */
function useLoad(path) {
  const q = usePolled({ key: [PLUGIN_ID, path], fn: () => rest(path), staleTime: 60000 })
  return { loading: q.loading, data: q.data || null, error: q.error && !q.data ? describeError(q.error) : null, raw: q.error, retry: q.refetch }
}

/* ------------------------------------------------------------------ pieces -- */

const KIND_ICON = { assignment: 'comment', skill: 'book', routine: 'watch', bot: 'robot', crew: 'organization' }

function Icon(name, cls, size) {
  return jsx(Codicon, { name, className: cls || '', size: size || 14 })
}

function Back({ label, onClick }) {
  return jsx(KitBack, { label, onClick })
}

/** A text link to an outside page. When the app can't open it, the address appears with a copy button. */
function Link({ href, children }) {
  const [shown, setShown] = useState(false)
  return jsxs('span', { className: 'bc-linkwrap', children: [
    jsxs('button', { type: 'button', className: 'hk-link', onClick: async () => { if (!(await openLink(href))) setShown(true) },
      children: [children, Icon('link-external', 'bc-ext', 11)] }),
    shown ? jsx(Address, { href }) : null,
  ] })
}

function Address({ href }) {
  return jsxs('span', { className: 'bc-address hk-enter', role: 'status', children: [
    jsx('span', { className: 'hk-mono', children: href }),
    jsx('button', { type: 'button', className: 'hk-link', onClick: () => copyText(href, 'Copied the address'), children: 'Copy' }),
  ] })
}

/** "Open on botcabinet.com", with the page's address beside it, and a copy fallback. */
function SiteButton({ href, label, variant }) {
  const [shown, setShown] = useState(false)
  return jsxs('div', { className: 'bc-sitebtn', children: [
    jsxs(Button, { variant: variant || 'secondary', onClick: async () => { if (!(await openLink(href))) setShown(true) },
      children: [label || 'Open on botcabinet.com', Icon('link-external', 'bc-ext', 11)] }),
    shown ? jsx(Address, { href }) : jsx('span', { className: 'bc-addr-hint hk-mono', children: siteAddress(href) }),
  ] })
}

function Credit({ credit }) {
  if (!credit) return null
  return jsxs('p', { className: 'bc-credit', children: [Icon('heart'), jsx(Link, { href: credit.href, children: credit.line })] })
}

function Label({ children }) {
  return jsx('div', { className: 'hk-eyebrow bc-eyebrow', children })
}

function Problem({ error, retry }) {
  const offline = error && (error.status === 404 || error.status === null)
  if (offline) {
    return jsx(NotConnected, { name: 'Cabinet', onRetry: retry, error: error.status ? `${error.status}: ${error.detail}` : error.detail,
      steps: jsx('p', { children: 'Turn the Cabinet plugin on in Capabilities, then reopen this page. The Cabinet, Crew Kits and Test Records still work without it' }) })
  }
  return jsx(Failure, { title: 'That didn’t work', body: String((error && error.detail) || 'Something went wrong'), onRetry: retry,
    error: error && error.status ? `${error.status}: ${error.detail}` : null })
}

/** A painted portrait from the site, with a shimmer until the picture has decoded. */
function Portrait({ bot, className, alt }) {
  const [ready, setReady] = useState(false)
  const src = portraitOf(bot)
  return jsx('span', { className: `bc-portrait${ready ? ' bc-ready' : ''}${className ? ` ${className}` : ''}`, children: src
    ? jsx('img', { src, alt: alt === undefined ? `Painted portrait of ${bot.name}` : alt, decoding: 'async', draggable: false,
      onLoad: () => setReady(true), ref: el => { if (el && el.complete && !ready) setReady(true) } })
    : jsx('span', { className: 'bc-portrait-initial', children: bot ? bot.name.slice(0, 1) : '?' }) })
}

/** A round portrait, for crews and lists. `you` draws the person who steps in. */
function Face({ bot, you, label, size = 36, ring }) {
  if (you) {
    return jsx('span', { className: 'bc-face bc-face-you', style: { '--bc-size': `${size}px` }, role: 'img', 'aria-label': label || 'You',
      children: Icon('person', '', Math.round(size * 0.46)) })
  }
  return jsx('span', { className: `bc-face${ring ? ' bc-face-ring' : ''}`, style: { '--bc-size': `${size}px` }, title: bot ? bot.name : label,
    children: jsx(Portrait, { bot, alt: bot ? bot.name : label }) })
}

function Faces({ names, size = 28 }) {
  return jsx('span', { className: 'bc-faces', children: names.map((n, i) => jsx(Face, { bot: botNamed(n), label: n, size }, n + i)) })
}

/** The site's own verdict for a Bot, as a badge: Tested or Not yet tested. */
function TestBadge({ bot, over }) {
  const tested = bot.test.status === 'tested'
  return jsxs('span', { className: `bc-badge${over ? ' bc-badge-over' : ''}`, 'data-tested': tested ? '' : undefined, children: [
    Icon(tested ? 'pass-filled' : 'circle-large-outline', '', 12), tested ? 'Tested' : 'Not yet tested',
  ] })
}

function CrewStatus({ crew }) {
  const tone = crewFailed(crew) ? 'bad' : crewPassed(crew) ? 'good' : 'idle'
  const text = CREW_TEST_LABEL[crew.testStatus] || crew.testLabel
  return jsx(Pill, { tone, dot: true, children: `Crew test: ${text.toLowerCase()}` })
}

/** Under every catalog page: where the Bots came from and how fresh they are, with a Refresh button. */
function StaleLine() {
  const c = useValue($catalog)
  const s = c.status
  const label = s ? s.label : `Showing the saved copy from ${shortDate(SNAPSHOT).replace(/, \d+$/, '')}`
  const live = Boolean(s && s.source === 'live')
  // Only a real problem gets a note (the backend marks it tone 'warn', a missing catalog file included). How the
  // copy got here is nothing the reader needs, so otherwise the line just says how fresh the Bots are.
  const warn = Boolean(s && s.tone === 'warn')
  const note = warn ? s.note || s.error || '' : ''
  return jsxs('p', { className: 'bc-source hk-small', role: 'status', 'aria-live': 'polite', 'data-tone': warn ? 'warn' : undefined, children: [
    Icon(warn ? 'warning' : live ? 'cloud' : 'archive', '', 12),
    jsx('strong', { children: label }),
    note ? jsx('span', { className: 'bc-source-note', children: note }) : null,
    jsx('span', { className: 'hk-grow' }),
    jsx('button', { type: 'button', className: 'hk-link', disabled: c.refreshing, onClick: () => loadCatalog(true),
      children: c.refreshing ? 'Checking botcabinet.com' : 'Refresh' }),
    jsx(Link, { href: SITE.origin, children: 'Visit botcabinet.com' }),
  ] })
}

/**
 * Only when this Hermes's gateway answers bots.catalog: where Hermes's own catalog is, and what Cabinet still does.
 * Bots the catalog carries are added there; Cabinet adds the rest, and still helps you choose and plan.
 */
function HandoverNotice({ bot }) {
  const hb = useValue($hermesBots)
  if (!hb.available) return null
  const title = bot ? carriedBy(hb, bot) : null
  const what = !bot ? 'Add a Bot it carries there. Cabinet adds the others and still helps you choose and plan'
    : title ? `It carries ${bot.name} as “${title}”, so add it there`
      : `It doesn’t carry ${bot.name}, so Cabinet adds this one for you below`
  return jsx(Notice, { tone: 'info', icon: 'sparkle', title: 'Your Hermes has its own Bots catalog',
    action: jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => openHermesBots(title ? bot : null, title), children: 'Open Hermes Bots' }),
    children: jsxs(Fragment, { children: [
      jsx('p', { children: what }),
      jsx('p', { children: 'Two ways to open it:' }),
      jsxs('ul', { className: 'bc-ul', children: [
        jsx('li', { children: 'Capabilities, in the Bots tab' }),
        jsx('li', { children: 'The + menu next to Bots in the sidebar, then “Browse Bot Marketplace…”' }),
      ] }),
    ] }) })
}

/* ------------------------------------------------------------------ frame -- */

const SECTIONS = [
  { id: 'start', label: 'Start Here', icon: 'home' },
  { id: 'fit', label: 'Fit Test', icon: 'checklist' },
  { id: 'cabinet', label: 'The Cabinet', icon: 'library' },
  { id: 'lab', label: 'Bot Lab', icon: 'beaker' },
  { id: 'crews', label: 'Crew Kits', icon: 'organization' },
  { id: 'mybots', label: 'My Bots', icon: 'briefcase' },
  { id: 'records', label: 'Test Records', icon: 'verified' },
]

function Tabs({ active }) {
  const refs = useRef([])
  const at = SECTIONS.findIndex(s => s.id === active)
  function onKeyDown(e) {
    const i = refs.current.indexOf(document.activeElement)
    if (i < 0) return
    let next = null
    if (e.key === 'ArrowRight') next = (i + 1) % SECTIONS.length
    else if (e.key === 'ArrowLeft') next = (i - 1 + SECTIONS.length) % SECTIONS.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = SECTIONS.length - 1
    if (next === null) return
    e.preventDefault()
    e.stopPropagation()
    refs.current[next] && refs.current[next].focus()
    go({ name: SECTIONS[next].id })
  }
  return jsx('nav', { className: 'bc-tabs', role: 'tablist', 'aria-label': 'Bot Cabinet sections', onKeyDown,
    children: SECTIONS.map((s, i) => jsxs('button', {
      type: 'button', role: 'tab', 'aria-selected': i === at, tabIndex: i === at || (at < 0 && i === 0) ? 0 : -1,
      className: 'bc-tab', ref: el => { refs.current[i] = el }, onClick: () => go({ name: s.id }),
      children: [Icon(s.icon, '', 13), s.label],
    }, s.id)) })
}

function TopBar({ view }) {
  const section = SECTION_OF[view.name] || 'start'
  const bot = (view.name === 'bot' || view.name === 'add') && BOT[view.id]
  const crew = (view.name === 'crew' || view.name === 'setup') && CREW[view.id]
  const here = bot ? bot.href : crew ? crew.href : SITE.pages[section] || SITE.origin
  return jsxs('div', { className: 'bc-top', children: [
    jsxs('div', { className: 'bc-top-row', children: [
      jsx('button', { type: 'button', className: 'bc-brand', onClick: () => go({ name: 'start' }), 'aria-label': 'Bot Cabinet, Start Here',
        children: jsx('span', { className: 'bc-wordmark', role: 'img', 'aria-label': 'Bot Cabinet' }) }),
      jsxs('span', { className: 'bc-tagline', children: [jsx('span', { className: 'bc-lamp', 'aria-hidden': 'true' }), SITE.tagline] }),
      jsx('span', { className: 'hk-grow' }),
      jsxs('button', { type: 'button', className: 'bc-here', onClick: async () => { if (!(await openLink(here))) copyText(here, 'The app couldn’t open it, so the address is on your clipboard') },
        children: [jsx('span', { className: 'hk-mono', children: siteAddress(here) }), Icon('link-external', '', 11)] }),
    ] }),
    jsx(Tabs, { active: section }),
  ] })
}

/** Frame: the kit page with Cabinet's top bar; Esc goes back (or leaves a text field first). */
function Frame({ children, back, keys, width = 'wide' }) {
  const root = useRef(null)
  useFocusOnMount(root)
  const onKeyDown = useKeys({
    Escape: e => { if (isTyping(e)) { e.target.blur(); return } if (back) back() },
    ...(keys || {}),
  })
  const view = useValue($view)
  return jsx(KitPage, { rootRef: root, width, onKeyDown, label: 'Bot Cabinet', className: 'bc', top: jsx(TopBar, { view }), children })
}

function Head({ eyebrow, title, lead, children, serif = true }) {
  return jsxs('header', { className: 'hk-head hk-enter bc-head', children: [
    eyebrow ? jsx(Label, { children: eyebrow }) : null,
    jsx('h1', { className: `hk-h1${serif ? ' bc-display' : ''}`, children: title }),
    lead ? jsx('p', { className: 'hk-lead', children: lead }) : null,
    children || null,
  ] })
}

/* ------------------------------------------------------------------ Start Here -- */

const LADDER_HEIGHTS = [26, 40, 54, 68, 82]

/** Assignment, Skill, Routine, Bot, Crew: five rungs, each a step up in how much the setup carries. */
function Ladder({ onPick, active }) {
  const n = SITE.ladder.length
  const points = SITE.ladder.map((_, i) => `${(i + 0.5) * (100 / n)},${100 - LADDER_HEIGHTS[i]}`).join(' ')
  return jsxs('div', { className: 'bc-ladder', role: 'list', 'aria-label': 'Five setups, smallest to largest', children: [
    jsx('svg', { className: 'bc-ladder-line', viewBox: '0 0 100 100', preserveAspectRatio: 'none', 'aria-hidden': 'true',
      children: jsx('polyline', { points, fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeDasharray: '3 3', vectorEffect: 'non-scaling-stroke' }) }),
    ...SITE.ladder.map((r, i) => jsxs(onPick ? 'button' : 'div', {
      type: onPick ? 'button' : undefined, role: 'listitem', className: `bc-rung${active === r.kind ? ' bc-rung-on' : ''}`, onClick: onPick ? () => onPick(r.kind) : undefined,
      'aria-label': `${String(i + 1).padStart(2, '0')} ${(KIND_TEXT[r.kind] || r).title || r.label}: ${(KIND_TEXT[r.kind] || {}).meaning || r.note}`,
      children: [
        jsx('span', { className: 'bc-rung-bar', style: { height: LADDER_HEIGHTS[i] }, children: jsx('span', { className: 'bc-rung-num hk-mono', children: String(i + 1).padStart(2, '0') }) }),
        jsxs('span', { className: 'bc-rung-label', children: [Icon(KIND_ICON[r.kind], '', 13), KIND_TEXT[r.kind] ? KIND_TEXT[r.kind].title : r.label] }),
        jsx('span', { className: 'bc-rung-note', children: KIND_TEXT[r.kind] ? KIND_TEXT[r.kind].meaning : r.note }),
      ],
    }, r.kind)),
  ] })
}

/**
 * An Add button for a Bot. Cabinet's own Add opens the review screen, and nothing is made until you press the button
 * there. A Bot Hermes's own catalog carries is added in Hermes Bots instead; one it already added gets no second copy.
 */
function AddButton({ bot, className, size = 'sm', variant = 'secondary', label }) {
  // null means the app's own default for that prop (the main button on a page).
  const look = { variant: variant === null ? undefined : variant, size: size === null ? undefined : size }
  const hb = useValue($hermesBots)
  const c = useValue($catalog)
  const cls = `bc-add${className ? ` ${className}` : ''}`
  if (addedBy(c, bot)) {
    return jsxs('span', { className: `${cls} bc-added`, role: 'status', children: [Icon('pass-filled', '', 12), 'Already added through Hermes Bots'] })
  }
  const title = carriedBy(hb, bot)
  if (title) {
    return jsxs(Button, { ...look, className: cls, 'aria-label': `Add ${bot.name} in Hermes Bots`,
      onClick: () => openHermesBots(bot, title), children: [Icon('arrow-right', '', 12), 'Add in Hermes Bots'] })
  }
  return jsxs(Button, { ...look, className: cls, 'aria-label': `Add ${bot.name} to my bots`,
    onClick: () => go({ name: 'add', id: bot.slug }), children: [Icon('add', '', 12), label || 'Add'] })
}

function BotTile({ bot, onOpen, compact }) {
  return jsxs('div', { className: 'bc-tilebox', children: [
    jsxs('button', { type: 'button', className: `bc-tile${compact ? ' bc-tile-compact' : ''}`, onClick: () => onOpen(bot),
      'aria-label': `${bot.name}, ${bot.title}. ${bot.test.status === 'tested' ? 'Tested' : 'Not yet tested'}`,
      children: [
        jsxs('span', { className: 'bc-tile-art', children: [jsx(Portrait, { bot, alt: '' }), jsx(TestBadge, { bot, over: true })] }),
        jsxs('span', { className: 'bc-tile-body', children: [
          jsx('span', { className: 'bc-tile-name', children: bot.name }),
          jsx('span', { className: 'bc-tile-title', children: bot.title }),
          compact ? null : jsx('span', { className: 'bc-tile-sum', children: bot.summary }),
          compact || !bot.credits.length ? null : jsxs('span', { className: 'bc-tile-credit', children: [Icon('heart', '', 11), jsx('span', { children: bot.credits[0].line })] }),
        ] }),
      ] }),
    jsx(AddButton, { bot, className: 'bc-add-tile' }),
  ] })
}

function Tally({ rows, label }) {
  const total = rows.reduce((s, r) => s + r.n, 0) || 1
  let x = 0
  return jsxs('div', { className: 'bc-tally', children: [
    jsx('svg', { className: 'bc-tally-bar', viewBox: '0 0 100 8', preserveAspectRatio: 'none', role: 'img', 'aria-label': label,
      children: rows.map(r => {
        const w = (r.n / total) * 100
        const el = r.n ? jsx('rect', { x, y: 0, width: Math.max(w - 0.6, 0.6), height: 8, rx: 1, className: `bc-tone-${r.tone}` }, r.label) : null
        x += w
        return el
      }) }),
    jsx('ul', { className: 'bc-tally-key', children: rows.map(r => jsxs('li', { children: [
      jsx('span', { className: `bc-key bc-tone-${r.tone}`, 'aria-hidden': 'true' }), jsx('strong', { children: String(r.n) }), ` ${r.label}`,
    ] }, r.label)) }),
  ] })
}

function botTally() {
  const reproduced = BOTS.filter(b => b.test.kind === 'reproduced').length
  const once = BOTS.filter(b => b.test.kind === 'one-run').length
  return [
    { label: 'passed two recorded task runs', n: reproduced, tone: 'good' },
    { label: 'passed one recorded task run', n: once, tone: 'info' },
    { label: 'installed, not yet tested on a task', n: BOTS.length - reproduced - once, tone: 'idle' },
  ]
}

function crewTally() {
  // Every crew lands in exactly one row, so the rows always add up to the number of Crew Kits.
  const passed = CREWS.filter(crewPassed).length
  const failed = CREWS.filter(crewFailed).length
  return [
    { label: 'passed a crew test', n: passed, tone: 'good' },
    { label: 'failed its crew test', n: failed, tone: 'bad' },
    { label: 'not yet tested as a crew', n: CREWS.length - passed - failed, tone: 'idle' },
  ]
}

function StartHere() {
  const featured = [...TESTED, ...BOTS.filter(b => b.test.status !== 'tested')].slice(0, 6)
  const crew = CREW['personal-planning-desk'] || CREWS[0]
  const openBot = b => go({ name: 'bot', id: b.slug })
  return jsx(Frame, { keys: { f: () => go({ name: 'fit' }), c: () => go({ name: 'cabinet' }) }, children: [
    jsxs('section', { className: 'bc-hero hk-enter', 'aria-labelledby': 'bc-hero-title', children: [
      jsx('img', { className: 'bc-hero-art', src: IMAGES.doors, alt: '', decoding: 'async', draggable: false }),
      jsx('span', { className: 'bc-hero-scrim', 'aria-hidden': 'true' }),
      jsxs('div', { className: 'bc-hero-copy', children: [
        jsx('p', { className: 'bc-kicker', children: 'Bot Cabinet' }),
        jsx('h1', { className: 'bc-display bc-hero-title', id: 'bc-hero-title', children: SITE.promise }),
        jsx('p', { className: 'bc-hero-deck', children: HERO_DECK }),
        jsxs('div', { className: 'hk-actions', children: [
          jsx(Button, { onClick: () => go({ name: 'fit' }), children: 'Take the Fit Test' }),
          jsx(Button, { variant: 'secondary', onClick: () => go({ name: 'cabinet' }), children: 'Browse The Cabinet' }),
          jsx(KeyHint, { keys: ['F'], label: 'Fit Test' }),
          jsx(KeyHint, { keys: ['C'], label: 'Cabinet' }),
        ] }),
        jsxs('p', { className: 'bc-hero-facts', children: [
          jsx('span', { children: `${BOTS.length} Bots` }), jsx('span', { children: `${CREWS.length} Crew Kits` }),
          jsx('span', { children: `${TESTED.length} with a recorded task run` }),
        ] }),
      ] }),
    ] }, 'hero'),
    jsxs(Card, { className: 'bc-panel', children: [
      jsxs('div', { className: 'bc-panel-head', children: [
        jsxs('div', { className: 'hk-stack', style: { gap: 2 }, children: [
          jsx(Label, { children: 'Bot Fit Test' }),
          jsx('h2', { className: 'hk-h2 bc-serif', children: 'Find the simplest setup that can do the job' }),
          jsx('p', { children: 'Describe the job and answer five quick questions. Cabinet suggests the smallest setup that does it' }),
        ] }),
        jsx(Button, { onClick: () => go({ name: 'fit' }), children: 'Start the Fit Test' }),
      ] }),
      jsx(Ladder, { onPick: () => go({ name: 'fit' }) }),
    ] }, 'ladder'),
    jsxs('section', { className: 'hk-section', children: [
      jsxs('div', { className: 'bc-sec-head', children: [
        jsx('h2', { className: 'hk-h2 bc-serif', children: 'From The Cabinet' }),
        jsx('span', { className: 'hk-grow' }),
        jsx('button', { type: 'button', className: 'hk-link', onClick: () => go({ name: 'cabinet' }), children: `See all ${BOTS.length} Bots` }),
      ] }),
      jsx('div', { className: 'bc-strip hk-enter-list', children: featured.map(b => jsx(BotTile, { bot: b, onOpen: openBot, compact: true }, b.slug)) }),
    ] }, 'bots'),
    jsxs('div', { className: 'bc-two', children: [
      jsxs(Card, { className: 'bc-panel', children: [
        jsx(Label, { children: 'Crew Kits' }),
        jsx('h2', { className: 'hk-h2 bc-serif', children: 'Bots that hand work to each other' }),
        jsx('p', { children: `${crew.name}: ${crew.summary}` }),
        jsx(CrewTrack, { crew, size: 'sm' }),
        jsx('div', { className: 'hk-actions', children: jsx(Button, { variant: 'secondary', onClick: () => go({ name: 'crews' }), children: 'See the Crew Kits' }) }),
      ] }),
      jsxs(Card, { className: 'bc-panel', children: [
        jsx(Label, { children: 'Test Records' }),
        jsx('h2', { className: 'hk-h2 bc-serif', children: 'What has been tested so far' }),
        jsx('p', { children: 'Each Bot page lists what was tested and what was not, failures included' }),
        jsx(Tally, { rows: botTally(), label: `${BOTS.length} Bots by test record` }),
        jsx('div', { className: 'hk-actions', children: jsx(Button, { variant: 'secondary', onClick: () => go({ name: 'records' }), children: 'Read the Test Records' }) }),
      ] }),
    ] }, 'two'),
    jsxs(Card, { className: 'bc-panel', children: [
      jsx(Label, { children: 'Learning Hermes' }),
      jsx('h2', { className: 'hk-h2 bc-serif', children: 'Community guides to Hermes' }),
      jsxs('ul', { className: 'bc-ul bc-plain', children: [
        jsxs('li', { children: [jsx(Link, { href: 'https://hermesatlas.com/', children: 'Hermes Atlas' }), ' · guides, a masterclass and the ecosystem map, by Kevin Simback'] }),
        jsxs('li', { children: [jsx(Link, { href: 'https://www.hermesbible.com/', children: 'Hermes Bible' }), ' · the Hermes docs, searchable, with community flows, by iamlukethedev'] }),
      ] }),
    ] }, 'learn'),
    jsx(HandoverNotice, {}, 'handover'),
    jsx(StaleLine, {}, 'stale'),
  ] })
}

/* ------------------------------------------------------------------ The Cabinet -- */

function matches(bot, q) {
  if (!q) return true
  const hay = [bot.name, bot.title, bot.summary, bot.categoryLabel, bot.outcome].join(' ').toLowerCase()
  return q.toLowerCase().split(/\s+/).filter(Boolean).every(w => hay.includes(w))
}

/** Arrow keys move between cards in a grid, row by row. */
function gridKeys(e) {
  const keys = ['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp', 'Home', 'End']
  if (!keys.includes(e.key)) return
  const items = [...e.currentTarget.querySelectorAll('.bc-tile')]
  const i = items.indexOf(document.activeElement)
  if (i < 0) return
  const top = items[0].offsetTop
  const cols = Math.max(1, items.filter(el => el.offsetTop === top).length)
  const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols }[e.key]
  const next = e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1 : i + step
  if (next < 0 || next >= items.length) return
  e.preventDefault()
  e.stopPropagation()
  items[next].focus()
}

function Cabinet() {
  const f = useValue($cabinet)
  const search = useRef(null)
  const set = patch => $cabinet.set({ ...f, ...patch })
  const shown = BOTS.filter(b => matches(b, f.q) && (f.status === 'all' || b.test.status === f.status) && (f.category === 'all' || b.category === f.category))
  const status = [
    { id: 'all', label: `All ${BOTS.length}` },
    { id: 'tested', label: `Tested ${TESTED.length}` },
    { id: 'not-tested', label: `Not yet tested ${BOTS.length - TESTED.length}` },
  ]
  return jsx(Frame, { keys: { '/': () => search.current && search.current.focus() }, children: [
    jsx(Head, { eyebrow: 'The Cabinet', title: `${BOTS.length} Bots ready to add`,
      lead: 'Open a Bot to see what it does and what has been tested, or press Add to put it in your Bots' }, 'head'),
    jsx(HandoverNotice, {}, 'handover'),
    jsxs('div', { className: 'bc-toolbar', role: 'search', children: [
      jsxs('label', { className: 'bc-search', children: [
        Icon('search', '', 13),
        jsx(Input, { ref: search, value: f.q, placeholder: 'Search by name or job', 'aria-label': 'Search the Bots', className: 'bc-search-input',
          onChange: e => set({ q: e.target.value }), onKeyDown: e => { if (e.key === 'Escape' && f.q) { e.preventDefault(); e.stopPropagation(); set({ q: '' }) } } }),
        jsx(KeyHint, { keys: ['/'] }),
      ] }),
      jsx(SegmentedControl, { options: status, value: f.status, onChange: id => set({ status: id }) }),
    ] }, 'tools'),
    jsxs('div', { className: 'bc-chips', role: 'group', 'aria-label': 'Kind of work', children: [
      jsx('button', { type: 'button', className: 'bc-chip', 'aria-pressed': f.category === 'all', onClick: () => set({ category: 'all' }), children: 'Every kind of work' }),
      ...SITE.categories.map(c => jsx('button', { type: 'button', className: 'bc-chip', 'aria-pressed': f.category === c.key,
        onClick: () => set({ category: f.category === c.key ? 'all' : c.key }), children: c.label }, c.key)),
    ] }, 'chips'),
    jsx('p', { className: 'hk-small bc-count', role: 'status', 'aria-live': 'polite', children: shown.length === BOTS.length ? `Showing all ${BOTS.length} Bots` : `Showing ${shown.length} of ${BOTS.length} Bots` }, 'count'),
    shown.length
      ? jsx('div', { className: 'bc-grid', onKeyDown: gridKeys, children: shown.map(b => jsx(BotTile, { bot: b, onOpen: x => go({ name: 'bot', id: x.slug }) }, b.slug)) }, 'grid')
      : jsx(Empty, { icon: 'search', title: f.q ? `Nothing in The Cabinet matches “${f.q}”` : 'Nothing matches these filters',
        body: 'Try a shorter word, or show every Bot. The Fit Test can also suggest a setup from a sentence about the job',
        action: jsxs('div', { className: 'hk-actions', style: { justifyContent: 'center' }, children: [
          jsx(Button, { variant: 'secondary', onClick: () => $cabinet.set({ q: '', status: 'all', category: 'all' }), children: 'Show every Bot' }),
          jsx(Button, { variant: 'secondary', onClick: () => go({ name: 'fit' }), children: 'Take the Fit Test' }),
        ] }) }, 'empty'),
    jsx(StaleLine, {}, 'stale'),
  ] })
}

/** A bot page's test lines in plain words. Wording the site adds later, and Cabinet does not know, shows as the site wrote it. */
const PLAIN_LINE = {
  'Hermes archive import checked; a full task run is still pending': 'It installs cleanly in Hermes. It has not been tested on a real task yet',
  'One recorded task run is available': 'It passed one recorded test on a task',
  'Two recorded task runs passed': 'It passed two recorded tests on a task',
}
function plainCheck(item) {
  const installed = /^The archive passed an isolated import and bundled-Skill presence check in Hermes Agent ([\d.]+) on (.+)$/.exec(item.detail || '')
  if (item.what === 'Hermes import check') {
    return { ...item, what: 'Installs in Hermes', detail: installed ? `It installed cleanly in Hermes ${installed[1]}, with all its skills in place` : item.detail }
  }
  const words = {
    'Two task runs': { what: 'Two tests on a task', detail: 'It passed its first job twice, and both results met every check' },
    'A real task': { what: 'Your own work', detail: 'Nobody has yet checked how it does on a real person\'s work' },
    'Human technical review': { what: 'An expert\'s review', detail: 'An expert has not reviewed it yet' },
    'Repeated runs': { what: 'Doing it again and again', detail: 'One good run does not show it will do the job every time, or that it is safe to put on a schedule' },
  }[item.what]
  if (words) return { ...item, ...words }
  if (item.what === 'One task run') {
    return { ...item, what: 'One test on a task', detail: String(item.detail || '').replace(/^One bounded first-mission run passed using supplied fictional material:\s*/, 'It passed one test on made-up material: ') }
  }
  return item
}

function TestRecord({ bot }) {
  return jsxs(Card, { className: 'bc-record', children: [
    jsxs('div', { className: 'bc-panel-head', children: [
      jsxs('div', { className: 'hk-stack', style: { gap: 2 }, children: [
        jsx('h2', { className: 'hk-h2', children: 'Test record' }),
        jsx('p', { children: PLAIN_LINE[bot.test.line] || bot.test.line }),
      ] }),
      jsx(TestBadge, { bot }),
    ] }),
    jsxs('div', { className: 'bc-record-cols', children: [
      jsxs('div', { className: 'bc-record-col', children: [
        jsx('h3', { className: 'hk-h3', children: 'Tested' }),
        jsx('ul', { className: 'bc-checks', children: bot.test.tested.map(plainCheck).map(t => jsxs('li', { 'data-done': '', children: [
          Icon('pass-filled', 'bc-check-icon', 14),
          jsxs('div', { children: [
            jsxs('strong', { children: [t.what, t.date ? jsx('span', { className: 'bc-date', children: ` · ${shortDate(t.date)}` }) : null] }),
            jsx('div', { children: t.detail }),
            t.href ? jsx(Link, { href: t.href, children: 'Read the test record' }) : null,
          ] }),
        ] }, t.what)) }),
      ] }),
      jsxs('div', { className: 'bc-record-col', children: [
        jsx('h3', { className: 'hk-h3', children: 'Not yet tested' }),
        jsx('ul', { className: 'bc-checks', children: bot.test.notTested.map(plainCheck).map(t => jsxs('li', { children: [
          Icon('circle-large-outline', 'bc-check-icon', 14),
          jsxs('div', { children: [jsx('strong', { children: t.what }), jsx('div', { children: t.detail })] }),
        ] }, t.what)) }),
      ] }),
    ] }),
    jsx('p', { className: 'hk-small bc-note', children: RECORDS_LEAD }),
  ] })
}

/** "Asks you before: Ask before sending" says it twice. Under that heading each line starts at the action. */
function askBefore(items) {
  const seen = new Set()
  return (items || []).map(x => {
    const t = String(x).replace(/^(always\s+)?ask( the owner| me| you)? before\s+/i, '')
    return t.charAt(0).toUpperCase() + t.slice(1)
  }).filter(t => (seen.has(t.toLowerCase()) ? false : seen.add(t.toLowerCase())))
}

/** A list of short items. A one-sentence item drops its closing period, so every line in a list reads the same. */
function List({ items }) {
  const tidy = x => (typeof x === 'string' && !/[.!?]\s+\S/.test(x.trim()) ? x.trim().replace(/\.$/, '') : x)
  return jsx('ul', { className: 'bc-ul', children: items.map(x => jsx('li', { children: tidy(x) }, x)) })
}

/** Text that is really a sequence (a first task told in three or four sentences): one numbered line per sentence. */
function sentences(text) {
  return String(text || '').trim().split(/(?<=[.!?])\s+(?=[A-Z$“"])/).map(t => t.trim()).filter(Boolean)
}
function Steps({ text }) {
  const parts = sentences(text)
  if (parts.length < 2) return jsx('p', { children: text })
  return jsx('ol', { className: 'bc-steps', children: parts.map((t, i) => jsx('li', { children: t }, i)) })
}

const lowerFirst = text => String(text || '').replace(/^([A-Z])(?=[a-z])/, c => c.toLowerCase())

function BotPage({ id }) {
  const bot = BOT[id]
  const i = BOTS.indexOf(bot)
  const back = () => go({ name: 'cabinet' })
  if (!bot) {
    return jsx(Frame, { back, children: jsx(Empty, { icon: 'robot', title: 'That Bot isn’t in this copy of The Cabinet',
      body: 'It may be newer than the copy Cabinet carries', action: jsx(Button, { variant: 'secondary', onClick: back, children: 'Back to The Cabinet' }) }) })
  }
  const prev = BOTS[(i - 1 + BOTS.length) % BOTS.length]
  const next = BOTS[(i + 1) % BOTS.length]
  const crews = CREWS_OF(bot.slug)
  const partners = bot.worksWith.map(s => BOT[s]).filter(Boolean)
  return jsx(Frame, { back, keys: { ArrowLeft: () => go({ name: 'bot', id: prev.slug }), ArrowRight: () => go({ name: 'bot', id: next.slug }) }, children: [
    jsxs('div', { className: 'hk-row', children: [
      jsx(Back, { label: 'The Cabinet', onClick: back }),
      jsx('span', { className: 'hk-grow' }),
      jsxs('button', { type: 'button', className: 'bc-step', onClick: () => go({ name: 'bot', id: prev.slug }), children: [Icon('chevron-left', '', 13), prev.name] }),
      jsxs('button', { type: 'button', className: 'bc-step', onClick: () => go({ name: 'bot', id: next.slug }), children: [next.name, Icon('chevron-right', '', 13)] }),
      jsx(KeyHint, { keys: ['←', '→'] }),
    ] }, 'nav'),
    jsxs('div', { className: 'bc-detail', children: [
      jsxs('aside', { className: 'bc-detail-side hk-enter', children: [
        jsxs('div', { className: 'bc-detail-art', children: [jsx(Portrait, { bot }), jsx(TestBadge, { bot, over: true })] }),
        jsx(AddButton, { bot, variant: null, size: null, label: 'Add to my bots' }),
        jsx(SiteButton, { href: bot.href, variant: 'secondary' }),
        jsx(Button, { variant: 'secondary', onClick: () => { $lab.set({ job: bot.outcome, plan: null, kept: {} }); go({ name: 'lab' }) }, children: 'Plan one like it in Bot Lab' }),
      ] }),
      jsxs('div', { className: 'hk-stack bc-detail-main', style: { gap: 12 }, children: [
        jsxs('header', { className: 'hk-head hk-enter bc-head', children: [
          jsx(Label, { children: bot.categoryLabel }),
          jsx('h1', { className: 'hk-h1 bc-display', children: bot.name }),
          jsx('p', { className: 'bc-subtitle', children: bot.title }),
          jsx('p', { className: 'hk-lead', children: bot.summary }),
          bot.audience ? jsxs('p', { children: [jsx('strong', { children: 'Best for ' }), lowerFirst(bot.audience)] }) : null,
          bot.credits.length ? jsx('div', { className: 'bc-credits', children: bot.credits.map(c => jsx(Credit, { credit: c }, c.href)) }) : null,
        ] }),
        jsx(TestRecord, { bot }),
        jsxs(Card, { children: [
          jsx('h2', { className: 'hk-h2', children: 'What it does' }),
          jsx(Facts, { rows: [
            { label: 'Its job', value: bot.outcome },
            { label: 'You give it', value: jsx(List, { items: bot.inputs }) },
            { label: 'You get back', value: jsx(List, { items: bot.outputs }) },
            bot.examples.length ? { label: 'Try asking', value: jsx(List, { items: bot.examples.slice(0, 2) }) } : null,
            { label: 'First task', value: jsx(Steps, { text: bot.firstMission }) },
          ] }),
        ] }),
        jsxs(Card, { children: [
          jsx('h2', { className: 'hk-h2', children: 'Where you stay in charge' }),
          jsx(Facts, { rows: [
            { label: 'Asks you before', value: jsx(List, { items: askBefore(bot.approvals) }) },
            { label: 'Off limits', value: jsx(List, { items: bot.offLimits }) },
          ] }),
        ] }),
        partners.length || crews.length ? jsxs(Card, { children: [
          jsx('h2', { className: 'hk-h2', children: 'Works with' }),
          partners.length ? jsx('div', { className: 'bc-partners', children: partners.map(p => jsxs('button', { type: 'button', className: 'bc-partner', onClick: () => go({ name: 'bot', id: p.slug }), children: [
            jsx(Face, { bot: p, size: 34 }), jsxs('span', { children: [jsx('strong', { children: p.name }), jsx('span', { className: 'hk-small', children: p.title })] }),
          ] }, p.slug)) }) : null,
          crews.length ? jsxs('div', { className: 'bc-crewlinks', children: [
            jsx('span', { className: 'hk-small', children: `In ${crews.length} Crew ${crews.length === 1 ? 'Kit' : 'Kits'}` }),
            ...crews.map(c => jsx('button', { type: 'button', className: 'bc-chip', onClick: () => go({ name: 'crew', id: c.slug }), children: c.name }, c.slug)),
          ] }) : null,
        ] }) : null,
        bot.learnMore.length ? jsxs(Card, { children: [
          jsx('h2', { className: 'hk-h2', children: 'Further reading by Hermes users' }),
          jsx('ul', { className: 'bc-ul bc-plain', children: bot.learnMore.map(l => jsxs('li', { children: [jsx(Link, { href: l.href, children: l.title }), ` · ${l.by}, on the Hermes Bible`] }, l.href)) }),
        ] }) : null,
      ] }),
    ] }, 'detail'),
  ] })
}

/* ------------------------------------------------------------------ Crew Kits -- */

// What Cabinet does and does not enforce for a crew, in Cabinet's own words (the site's line uses its own jargon).
const CREW_PERMISSIONS = [
  'Cabinet writes the limits above into each bot’s instructions',
  'The bots follow them as instructions, not locks: Hermes does not block those actions',
  'You set Hermes settings and access to connected accounts yourself',
]

/** A crew's handoff order: portraits along a path, numbered, with the person who steps in marked. */
function CrewTrack({ crew, size = 'lg' }) {
  const steps = crew.steps
  const n = steps.length
  const lg = size === 'lg'
  const node = lg ? 56 : 42
  let d = `M 50 50`
  for (let i = 0; i < n - 1; i++) {
    const x0 = i * 100 + 50
    const x1 = x0 + 100
    const lift = i % 2 ? 34 : -34
    d += ` C ${x0 + 40} ${50 + lift}, ${x1 - 40} ${50 + lift}, ${x1} 50`
  }
  return jsxs('div', { className: 'bc-track', 'data-size': size, style: { '--bc-n': n, '--bc-node': `${node}px` }, children: [
    jsxs('div', { className: 'bc-track-rail', children: [
      jsx('svg', { className: 'bc-track-line', viewBox: `0 0 ${n * 100} 100`, preserveAspectRatio: 'none', 'aria-hidden': 'true',
        children: jsx('path', { d, fill: 'none', stroke: 'currentColor', strokeWidth: lg ? 2 : 1.5, strokeDasharray: lg ? '5 4' : '3 3', vectorEffect: 'non-scaling-stroke' }) }),
      ...steps.slice(0, -1).map((s, i) => jsx('span', { className: 'bc-track-arrow', style: { left: `${((i + 1) / n) * 100}%` }, 'aria-hidden': 'true', children: Icon('chevron-right', '', lg ? 14 : 11) }, `a${i}`)),
    ] }),
    jsx('ol', { className: 'bc-track-steps', 'aria-label': `${crew.name}, handoff order`, children: steps.map(s => {
      const you = s.who.some(w => w.you)
      const bots = s.who.filter(w => w.bot)
      const label = `Step ${s.n}, ${s.step}: ${s.who.map(w => (w.bot ? BOT[w.bot].name : w.label === 'Person' ? 'you' : w.label)).join(' and ')}${lg ? `. ${s.action}` : ''}`
      return jsxs('li', { className: `bc-track-step${you ? ' bc-you' : ''}`, 'aria-label': label, children: [
        jsxs('span', { className: 'bc-track-node', children: [
          ...bots.slice(0, 3).map((w, k) => jsx(Face, { bot: BOT[w.bot], size: k ? Math.round(node * 0.62) : node, ring: true }, w.bot + k)),
          you ? jsx(Face, { you: true, label: 'You step in', size: bots.length ? Math.round(node * 0.62) : node }) : null,
          jsx('span', { className: 'bc-track-n', children: String(s.n) }),
        ] }),
        lg ? jsxs('span', { className: 'bc-track-cap', children: [
          jsx('strong', { children: s.step }),
          jsx('span', { className: 'bc-track-owner', children: s.who.map(w => (w.bot ? BOT[w.bot].name : w.label === 'Person' ? 'You' : w.label)).join(' + ') }),
          jsx('span', { className: 'bc-track-act', children: s.action }),
          you ? jsxs('span', { className: 'bc-stepin', children: [Icon('person', '', 11), 'You step in'] }) : null,
        ] }) : jsx('span', { className: 'bc-track-mini', children: s.step }),
      ] }, s.n)
    }) }),
  ] })
}

function timesWord(n) {
  return n === 1 ? 'once' : n === 2 ? 'twice' : `${n} times`
}

function Crews() {
  const failed = CREWS.filter(crewFailed).length
  const untested = CREWS.filter(c => !crewPassed(c) && !crewFailed(c)).length
  return jsx(Frame, { children: [
    jsx(Head, { eyebrow: 'Crew Kits', title: 'Bots that hand work to each other',
      lead: `Each crew shows who does what, in what order, and where you step in. ${untested} of ${CREWS.length} have not been tested as a crew yet${failed ? `, and ${failed} failed ${failed === 1 ? 'its' : 'their'} crew test` : ''}` }, 'head'),
    jsx('div', { className: 'bc-crewlist hk-enter-list', children: CREWS.map(c => jsxs('button', { type: 'button', className: 'bc-crewrow', onClick: () => go({ name: 'crew', id: c.slug }), children: [
      jsxs('span', { className: 'bc-crewrow-copy', children: [
        jsx('span', { className: 'bc-crewrow-name', children: c.name }),
        jsx('span', { className: 'bc-crewrow-sum', children: c.summary }),
        jsxs('span', { className: 'hk-row', children: [jsx(CrewStatus, { crew: c }), jsx('span', { className: 'hk-small', children: `${c.members.length} Bots · ${c.steps.length} steps · you step in ${timesWord(c.steps.filter(s => s.who.some(w => w.you)).length)}` })] }),
      ] }),
      jsx(CrewTrack, { crew: c, size: 'sm' }),
    ] }, c.slug)) }, 'list'),
    jsx(StaleLine, {}, 'stale'),
  ] })
}

function CrewPage({ id, from }) {
  const c = CREW[id]
  const back = () => go(from ? { name: 'answer', data: from } : { name: 'crews' })
  if (!c) {
    return jsx(Frame, { back, children: jsx(Empty, { icon: 'organization', title: 'That crew isn’t in this copy of Cabinet',
      action: jsx(Button, { variant: 'secondary', onClick: back, children: 'Back to the Crew Kits' }) }) })
  }
  return jsx(Frame, { back, children: [
    jsx(Back, { label: from ? 'Back to the answer' : 'Crew Kits', onClick: back }, 'back'),
    jsxs('header', { className: 'hk-head hk-enter bc-head', children: [
      jsx(Label, { children: 'Crew Kit' }),
      jsx('h1', { className: 'hk-h1 bc-display', children: c.name }),
      jsx('p', { className: 'hk-lead', children: c.summary }),
      jsxs('div', { className: 'hk-row', children: [
        jsx(CrewStatus, { crew: c }),
        c.testDate ? jsx('span', { className: 'hk-small', children: `Checked ${shortDate(c.testDate)}` }) : null,
        c.testHref ? jsx(Link, { href: c.testHref, children: 'Read the test record' }) : null,
      ] }),
      jsxs('div', { className: 'hk-actions', children: [
        jsxs(Button, { onClick: () => go({ name: 'setup', id: c.slug }), children: [Icon('organization', '', 13), 'Set up this crew'] }),
        jsx('span', { className: 'hk-small', children: 'You review every bot and every handoff first. Nothing is made until you press the button there' }),
      ] }),
    ] }, 'head'),
    jsx(FailedCrewNotice, { crew: c }, 'failed'),
    jsxs(Card, { className: 'bc-panel', children: [
      jsx('h2', { className: 'hk-h2', children: 'Who does what, in order' }),
      jsx('p', { className: 'hk-small', children: 'Work passes left to right. A marked step is where the crew stops and waits for you' }),
      jsx(CrewTrack, { crew: c }),
    ] }, 'track'),
    jsxs(Card, { children: [
      jsx('h2', { className: 'hk-h2', children: `The ${c.members.length} Bots in this crew` }),
      jsx('ul', { className: 'bc-members', children: c.members.map(m => jsx('li', { children: jsxs('button', { type: 'button', className: 'bc-member', onClick: () => go({ name: 'bot', id: m.slug }), children: [
        jsx(Face, { bot: BOT[m.slug], size: 44 }),
        jsxs('span', { className: 'hk-grow', children: [
          jsxs('span', { className: 'hk-row', children: [jsx('strong', { children: m.name }), BOT[m.slug] ? jsx(TestBadge, { bot: BOT[m.slug] }) : null] }),
          jsx('span', { className: 'bc-member-job', children: m.responsibility }),
        ] }),
        Icon('chevron-right', '', 13),
      ] }) }, m.slug)) }),
    ] }, 'members'),
    jsx(Card, { children: jsx(Facts, { rows: [
      { label: 'Asks you before', value: jsx(List, { items: c.approvals }) },
      { label: 'Off limits', value: jsx(List, { items: c.offLimits }) },
      { label: 'Permissions', value: jsx(List, { items: CREW_PERMISSIONS }) },
      { label: 'Further reading', value: jsx('ul', { className: 'bc-ul bc-plain', children: c.learnMore.map(l => jsxs('li', { children: [jsx(Link, { href: l.href, children: l.title }), ` · ${l.by}, on the Hermes Bible`] }, l.href)) }) },
    ] }) }, 'facts'),
    jsx(SiteButton, { href: c.href }, 'open'),
  ] })
}

/* ------------------------------------------------------------------ Test Records -- */

// botcabinet.com writes its test records for testers. Cabinet shows the same facts in plain words. A record or
// label the site adds later, and Cabinet has no plain version of, falls back to the site's own wording.
const PLAIN_STATE = {
  'test-designed': { label: 'Test planned', meaning: 'The test is written. It has not run yet, because something it needs is still missing.' },
  'test-prepared': { label: 'Test ready', meaning: 'Everything the test needs is ready. It has not run yet.' },
  'recorded-excerpt': { label: 'Partly recorded', meaning: 'Part of a run was recorded. The full record is still to come.' },
  'prompt-contract-recorded': { label: 'Run twice, one passed', meaning: 'Real results are saved, from the Bot\'s instructions run in Hermes rather than the downloadable Bot itself.' },
  'failed-runtime': { label: 'Failed its test', meaning: 'The test ran and the result did not pass its checks.' },
  'runtime-passed': { label: 'Passed once', meaning: 'The downloadable Bot passed its test once. A repeat run and a person\'s approval are separate steps.' },
  'reproduced': { label: 'Passed twice', meaning: 'The same Bot, on the same task, passed its checks a second time.' },
}
const PLAIN_RECORD = {
  'scout-research-brief': {
    detail: 'only part of the run was recorded',
    outcome: 'A short guide for someone new to Hermes',
    summary: 'A video shows part of one Scout conversation and its result. A full run that someone else can repeat is still to be recorded.',
  },
  'writer-article-draft': {
    detail: 'not run yet',
    outcome: 'Planned: a draft that sticks to an approved brief, with headline options and questions for the author',
    summary: 'The test is written. It still needs an approved brief to work from, and Hermes has not run it.',
  },
  'publishing-desk-failed-handoff': {
    label: 'Not passed yet', tone: 'warn',
    detail: 'passed once, then a repeat run found a gap',
    outcome: 'Five Bots take a newsletter from research to a checked draft, then wait for a person to approve it',
    summary: 'Scout, Researcher, Story, Writer and Editor ran the job in order. The Editor caught three claims the sources did not support, the Writer fixed them, and a fresh check passed all 25 of its rules. A person still has to approve the draft, and a repeat run found information missing, so the crew counts as not passed.',
  },
  'chief-of-staff-operating-brief': {
    detail: 'the second run added things nobody said',
    outcome: 'Turns a meeting into priorities, work, decisions and ideas',
    summary: 'Hermes ran this twice. The first run passed every check. The second kept the facts right but added a deadline and an owner nobody mentioned, so it does not count as repeatable yet.',
  },
  'curator-lineup-review': {
    detail: 'same result both times',
    outcome: 'Spots Bots in a lineup that overlap, without making up how well they perform',
    summary: 'Curator was installed in Hermes and ran the same made-up lineup review twice. Both runs passed every check.',
  },
  'reentry-project-resumption': {
    detail: 'same result both times',
    outcome: 'Keeps a newer proposal apart from the work you already approved',
    summary: 'Reentry was installed in Hermes and ran the same made-up project twice. Both runs passed every check.',
  },
  'receipt-refund-case': {
    detail: 'same result both times',
    outcome: 'Keeps approved, issued and received as three separate facts',
    summary: 'Receipt was installed in Hermes and ran the same made-up refund case twice. Both runs passed every check.',
  },
}
const RECORDS_LEAD = 'These are lab tests on made-up examples, one task each. Check a Bot’s work yourself before you rely on it or let it run on its own'

function plainRecord(r) {
  const state = PLAIN_STATE[r.state]
  const words = PLAIN_RECORD[r.slug] || {}
  return { ...r, tone: words.tone, stateLabel: words.label || state?.label || r.stateLabel, stateDetail: words.detail || r.stateDetail, outcome: words.outcome || r.outcome, summary: words.summary || r.summary }
}

const STATE_TONE = { reproduced: 'good', 'runtime-passed': 'info', 'failed-runtime': 'bad', 'prompt-contract-recorded': 'warn', 'recorded-excerpt': 'warn', 'test-designed': 'idle', 'test-prepared': 'idle' }

function RecordCard({ r: raw }) {
  const r = plainRecord(raw)
  const crew = r.crew ? CREWS.find(c => r.slug.startsWith(c.slug)) : null
  const bot = BOT[r.bot]
  return jsxs('article', { className: 'bc-proof', children: [
    jsx('div', { className: 'bc-proof-art', children: crew
      ? jsx('span', { className: 'bc-proof-faces', children: crew.members.map(m => jsx(Face, { bot: BOT[m.slug], size: 38, ring: true }, m.slug)) })
      : jsx(Portrait, { bot, alt: bot ? bot.name : '' }) }),
    jsxs('div', { className: 'bc-proof-body', children: [
      jsxs('div', { className: 'hk-row', children: [jsx(Pill, { tone: r.tone || STATE_TONE[r.state] || 'idle', dot: true, children: r.stateLabel }), jsx('span', { className: 'hk-small', children: r.stateDetail })] }),
      jsx('h3', { className: 'hk-h3 bc-proof-title', children: r.title }),
      jsx('p', { className: 'bc-proof-out', children: r.outcome }),
      jsx('p', { className: 'hk-small', children: r.summary }),
      jsx(Link, { href: r.href, children: 'Inspect the test record on botcabinet.com' }),
    ] }),
  ] })
}

function Records() {
  const runs = SITE.records.filter(r => r.state !== 'test-designed' && r.state !== 'test-prepared')
  const plans = SITE.records.filter(r => r.state === 'test-designed' || r.state === 'test-prepared')
  const waiting = BOTS.filter(b => b.test.status !== 'tested')
  const crewsWaiting = CREWS.filter(c => !crewPassed(c))
  return jsx(Frame, { children: [
    jsx(Head, { eyebrow: 'Test Records', title: 'What has been tested, and what has not', lead: RECORDS_LEAD }, 'head'),
    jsxs('div', { className: 'bc-two', children: [
      jsxs(Card, { className: 'bc-panel', children: [
        jsx(Label, { children: `${BOTS.length} Bots` }),
        jsx('h2', { className: 'hk-h2 bc-serif', children: `${TESTED.length} have a recorded task run` }),
        jsx(Tally, { rows: botTally(), label: 'Bots by test record' }),
        jsx('p', { className: 'hk-small', children: 'Every Bot installs in Hermes. That alone says nothing about how well it does its job' }),
      ] }),
      jsxs(Card, { className: 'bc-panel', children: [
        jsx(Label, { children: `${CREWS.length} Crew Kits` }),
        jsx('h2', { className: 'hk-h2 bc-serif', children: `${CREWS.filter(crewPassed).length} have passed a crew test` }),
        jsx(Tally, { rows: crewTally(), label: 'Crew Kits by test record' }),
        jsx('p', { className: 'hk-small', children: 'A crew test runs the whole crew, in order, on one sample job' }),
      ] }),
    ] }, 'tally'),
    jsxs('section', { className: 'hk-section', children: [
      jsxs('div', { className: 'bc-sec-head', children: [jsx('h2', { className: 'hk-h2 bc-serif', children: 'Tests that ran' }), jsx('span', { className: 'hk-count', children: String(runs.length) })] }),
      jsx('div', { className: 'bc-proofs hk-enter-list', children: runs.map(r => jsx(RecordCard, { r }, r.slug)) }),
    ] }, 'runs'),
    plans.length ? jsxs('section', { className: 'hk-section', children: [
      jsxs('div', { className: 'bc-sec-head', children: [jsx('h2', { className: 'hk-h2 bc-serif', children: 'Tests not run yet' }), jsx('span', { className: 'hk-count', children: String(plans.length) })] }),
      jsx('div', { className: 'bc-proofs', children: plans.map(r => jsx(RecordCard, { r }, r.slug)) }),
    ] }, 'plans') : null,
    jsxs(Card, { className: 'bc-panel', children: [
      jsx('h2', { className: 'hk-h2 bc-serif', children: 'Still waiting for a task test' }),
      jsx('p', { children: `${waiting.length} Bots and ${crewsWaiting.length} Crew Kits have no passing task test yet` }),
      jsx('div', { className: 'bc-waiting', children: waiting.map(b => jsxs('button', { type: 'button', className: 'bc-wait', onClick: () => go({ name: 'bot', id: b.slug }), children: [
        jsx(Face, { bot: b, size: 30 }), jsx('span', { children: b.name }),
      ] }, b.slug)) }),
      jsx('div', { className: 'bc-crewlinks', children: crewsWaiting.map(c => jsxs('button', { type: 'button', className: 'bc-chip', onClick: () => go({ name: 'crew', id: c.slug }), children: [
        crewFailed(c) ? Icon('error', 'bc-bad', 12) : Icon('circle-large-outline', '', 12), c.name,
      ] }, c.slug)) }),
    ] }, 'waiting'),
    jsx(Details, { label: 'What each record label means', children: jsx(Facts, { rows: Object.values(PLAIN_STATE).map(item => ({ label: item.label, value: item.meaning })) }) }, 'legend'),
  ] })
}

/** Said wherever a failed crew can be set up: the published crew test failed, so set it up to try it, not to rely on it. */
function FailedCrewNotice({ crew }) {
  if (!crew || !crewFailed(crew)) return null
  return jsx(Notice, { tone: 'warn', icon: 'warning', title: 'This crew failed its crew test',
    children: jsxs(Fragment, { children: [
      `It was run as a whole crew${crew.testDate ? ` on ${shortDate(crew.testDate)}` : ''} and did not pass. You can still set it up to try it, but check every handoff yourself before relying on it. `,
      crew.testHref ? jsx(Link, { href: crew.testHref, children: 'Read what went wrong' }) : null,
    ] }) })
}

function Notice({ tone, icon, title, children, action }) {
  return jsxs('div', { className: 'bc-notice hk-enter', 'data-tone': tone, children: [
    Icon(icon, '', 16),
    jsxs('div', { className: 'hk-stack', style: { gap: 2 }, children: [jsx('strong', { children: title }), jsx('div', { className: 'bc-notice-body', children }),
      action ? jsx('div', { className: 'hk-actions bc-notice-action', children: action }) : null] }),
  ] })
}

/* ------------------------------------------------------------------ the Fit Test -- */

function Choice({ q, value, onPick }) {
  const options = q.choices.map(c => ({ id: String(c.value), label: c.label }))
  return jsxs('div', { className: 'bc-q', role: 'group', 'aria-label': q.text, children: [
    jsxs('div', { className: 'bc-q-text', children: [q.text, value === undefined ? jsx(Pill, { tone: 'warn', children: 'Needs an answer' }) : null] }),
    jsx(SegmentedControl, { className: 'bc-seg', options, value: value === undefined ? '' : String(value),
      onChange: id => { const c = q.choices.find(x => String(x.value) === id); if (c) onPick(c.value) } }),
  ] })
}

function TextField({ q, value, onChange, multiline }) {
  const Control = multiline ? Textarea : Input
  return jsx(Field, { label: q.text, children: jsx(Control, { value: value || '', placeholder: q.help || '', rows: multiline ? 2 : undefined,
    'aria-label': q.text, onChange: e => onChange(e.target.value) }) })
}

const FIT_SHAPE = [{ title: true, lines: 5, button: true }]

function FitBanner() {
  return jsxs('header', { className: 'bc-banner hk-enter', children: [
    jsxs('div', { className: 'hk-stack', style: { gap: 4 }, children: [
      jsx(Label, { children: 'Bot Fit Test' }),
      jsx('h1', { className: 'hk-h1 bc-display', children: 'What do you need done?' }),
      jsx('p', { className: 'hk-lead', children: 'Describe the job and answer five quick questions. Cabinet suggests the smallest setup that does it' }),
    ] }),
    jsx('div', { className: 'bc-banner-ladder', children: jsx(Ladder, {}) }),
  ] })
}

function FitForm() {
  const form = useLoad('fit')
  const answers = useValue($answers)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  if (form.loading) return jsx(Frame, { children: [jsx(FitBanner, {}, 'b'), jsx(SkeletonPage, { label: 'Loading the questions', header: false, cards: FIT_SHAPE }, 's')] })
  if (form.error) return jsx(Frame, { children: [jsx(FitBanner, {}, 'b'), jsx(Problem, { error: form.error, retry: form.retry }, 'p')] })
  const f = form.data
  const byId = Object.fromEntries(f.questions.map(q => [q.id, q]))
  const set = (id, v) => setAnswers({ ...answers, [id]: v })
  const left = f.required.filter(id => (id === 'result' ? !String(answers.result || '').trim() : answers[id] === undefined))
  const scheduled = answers.frequency === 'scheduled'

  async function submit() {
    if (left.length || busy) return
    setBusy(true)
    setError(null)
    try {
      const out = await post('fit', answers)
      go({ name: 'answer', data: out })
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }

  const done = f.required.length - left.length
  return jsx(Frame, { keys: { 'mod+Enter': submit }, children: [
    jsx(FitBanner, {}, 'banner'),
    jsxs('div', { className: 'hk-split', children: [
      jsxs('div', { className: 'hk-stack', style: { gap: 14 }, children: [
        jsxs(Card, { className: 'bc-form', children: [
          jsx(Field, { label: 'The job, in a sentence', children: jsx(Textarea, { className: 'bc-job', rows: 2, value: answers.result || '', placeholder: byId.result.help,
            'aria-label': 'The job, in a sentence', onChange: e => set('result', e.target.value), ...submitOnModEnter(submit) }) }),
          ...['frequency', 'needsContinuingContext', 'needsMultipleSpecialists', 'workProvenManually', 'overlapsExistingRole']
            .map(id => jsx(Choice, { q: byId[id], value: answers[id], onPick: v => set(id, v) }, id)),
          scheduled ? jsxs(Card, { tone: 'warn', tint: true, className: 'hk-enter', children: [
            jsx('h3', { className: 'hk-h3', children: 'Before it runs on its own' }),
            jsx('p', { children: 'A schedule needs all four. Leave any blank and Cabinet tells you what’s still missing' }),
            jsx('div', { className: 'bc-grid2', children: f.routineDetails.map(id => jsx(TextField, { q: byId[id], value: answers[id], onChange: v => set(id, v) }, id)) }),
          ] }) : null,
          jsx('div', { className: 'bc-details', children: jsx(Details, { label: 'Limits (optional)', children:
            jsx('div', { className: 'bc-grid2', children: ['access', 'approvals'].map(id => jsx(TextField, { q: byId[id], value: answers[id], multiline: true, onChange: v => set(id, v) }, id)) }) }) }),
          error ? jsx(Problem, { error }) : null,
          jsxs('div', { className: 'bc-submit', children: [
            jsx(Meter, { value: done, max: f.required.length, tone: left.length ? 'info' : 'good',
              label: left.length ? (left.length === 1 ? `1 answer to go: ${byId[left[0]].text}` : `${left.length} answers to go`) : 'Every answer is in',
              detail: `${done} of ${f.required.length}` }),
            jsxs('div', { className: 'hk-actions', children: [
              jsx(Button, { onClick: submit, loading: busy, disabled: left.length > 0, children: 'Find the right setup' }),
              left.length ? null : jsx(KeyHint, { keys: [modKey(), '↵'] }),
              Object.keys(answers).length ? jsx('button', { type: 'button', className: 'hk-link', onClick: () => setAnswers({}), children: 'Start over' }) : null,
            ] }),
          ] }),
        ] }),
        jsx(Credit, { credit: { line: f.credit.line, href: f.credit.href } }),
      ] }),
      jsxs('aside', { className: 'hk-stack bc-side', style: { gap: 10 }, children: [
        jsx(Label, { children: 'Start from a common job' }),
        jsx('div', { className: 'bc-jobs', children: f.commonJobs.map(job => {
          const lead = botNamed(job.lead)
          return jsxs('button', { type: 'button', className: 'bc-job-card', onClick: () => setAnswers({ ...job.answers }), children: [
            lead ? jsx(Face, { bot: lead, size: 34 }) : null,
            jsxs('span', { className: 'bc-job-copy', children: [
              jsx('span', { className: 'bc-job-title', children: job.title }),
              jsxs('span', { className: 'bc-job-meta', children: ['Led by ', job.lead, job.optional.length ? ` · add ${job.optional.join(' or ')} later` : ''] }),
              job.credit ? jsx('span', { className: 'bc-job-credit', children: job.credit.line }) : null,
            ] }),
          ] }, job.slug)
        }) }),
        jsx('p', { className: 'hk-small', children: 'Picking one fills in every answer. Change any of them before you press the button' }),
        jsx('div', { className: 'bc-flows', children: jsx(Link, { href: f.communityFlows.href, children: f.communityFlows.label }) }),
        jsx('p', { className: 'hk-small', children: f.communityFlows.by }),
      ] }),
    ] }, 'body'),
  ] })
}

/* ------------------------------------------------------------------ the answer -- */

/** The five setups as a scale from smallest to largest, with the one this job needs lit. */
function KindScale({ out }) {
  const kinds = out.kinds || []
  const at = kinds.findIndex(k => k.kind === out.kind)
  return jsx(Stepper, { select: true, at, tone: 'info', compact: true, label: 'The five setups, smallest to largest',
    steps: kinds.map(k => ({ id: k.kind, icon: KIND_ICON[k.kind], title: k.title })) })
}

/** The answer path (SVG rail): each answer that led here, in order, ending at the setup it chose. */
function AnswerPath({ out }) {
  const rows = out.dependedOn
  const link = jsx('svg', { className: 'bc-path-link', viewBox: '0 0 2 100', preserveAspectRatio: 'none', 'aria-hidden': 'true',
    children: jsx('line', { x1: 1, x2: 1, y1: 0, y2: 100, stroke: 'currentColor', strokeWidth: 2, vectorEffect: 'non-scaling-stroke' }) })
  return jsxs('ol', { className: 'bc-path', 'aria-label': 'How your answers led to this setup', children: [
    ...rows.map((r, i, all) => jsxs('li', { className: 'bc-path-row', children: [
      jsxs('span', { className: 'bc-path-rail', children: [
        jsx('svg', { className: 'bc-path-node', width: 12, height: 12, viewBox: '0 0 12 12', 'aria-hidden': 'true', children: jsx('circle', { cx: 6, cy: 6, r: 4.5, fill: 'currentColor' }) }),
        link,
      ] }),
      jsxs('div', { className: 'bc-path-body', children: [
        jsx('div', { className: 'bc-why-q', children: r.question }),
        jsxs('div', { children: ['You said ', jsx('strong', { children: r.answer })] }),
        // Answers read together (the four schedule details) share one reason, shown once under the last.
        all[i + 1] && all[i + 1].effect === r.effect ? null : jsx('div', { className: 'bc-why-effect', children: r.effect }),
      ] }),
    ] }, r.id)),
    jsxs('li', { className: 'bc-path-row bc-path-end', children: [
      jsx('span', { className: 'bc-path-rail', children: jsx('span', { className: 'bc-path-goal', children: Icon(KIND_ICON[out.kind]) }) }),
      jsxs('div', { className: 'bc-path-body', children: [jsx('strong', { children: out.title }), jsx('div', { children: out.meaning })] }),
    ] }, 'end'),
  ] })
}

function WhyPanel({ out }) {
  return jsxs(Card, { children: [
    jsx('h2', { className: 'hk-h2', children: 'Why this answer' }),
    jsx('p', { className: 'hk-small', children: 'Each answer that led here, with the question it came from' }),
    jsx(AnswerPath, { out }),
    out.notUsed.length ? jsxs(Fragment, { children: [
      jsx('h3', { className: 'hk-h3', children: 'These didn’t change the answer' }),
      jsx('ul', { className: 'bc-unused', children: out.notUsed.map(r => jsxs('li', { children: [r.question, ' ', jsx('strong', { children: r.answer })] }, r.id)) }),
    ] }) : null,
    jsx('div', { className: 'hk-actions', children: jsx(Button, { variant: 'secondary', onClick: () => go({ name: 'fit' }), children: 'Change answers' }) }),
  ] })
}

/** A published Bot, inline: portrait, name, test badge, and a way to its page. */
function BotLine({ bot, heading, note }) {
  const site = BOT[bot.slug]
  return jsxs('div', { className: 'bc-botline', children: [
    site ? jsx('span', { className: 'bc-botline-art', children: jsx(Portrait, { bot: site, alt: '' }) }) : null,
    jsxs('div', { className: 'hk-grow hk-stack', style: { gap: 2 }, children: [
      jsxs('div', { className: 'hk-row', children: [jsx('strong', { children: heading }), site ? jsx(TestBadge, { bot: site }) : null] }),
      jsx('div', { children: note || `${bot.title} · ${bot.summary}` }),
      bot.credit ? jsx('div', { className: 'bc-job-credit', children: jsx(Link, { href: bot.credit.href, children: bot.credit.line }) }) : null,
    ] }),
    jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => go({ name: 'bot', id: bot.slug }), children: `See ${bot.name}` }),
  ] })
}

function MatchCard({ out }) {
  const m = out.match
  if (out.kind === 'crew' && m.crew) {
    const c = m.crew
    const site = CREW[c.slug]
    return jsxs(Card, { tone: 'info', className: 'hk-enter', children: [
      jsxs('div', { className: 'hk-row', children: [
        jsxs('div', { className: 'hk-grow', children: [jsx(Label, { children: 'Closest published crew' }), jsx('h2', { className: 'hk-h2 bc-serif', children: c.name })] }),
        site ? jsx(CrewStatus, { crew: site }) : jsx(Status, { status: c.testStatus, text: c.tested }),
      ] }),
      jsx('p', { children: c.summary }),
      site ? jsx(CrewTrack, { crew: site, size: 'sm' }) : jsxs('div', { className: 'hk-row', children: [jsx(Faces, { names: c.members.map(x => x.name) }),
        jsx('span', { children: `${c.members.map(x => x.name).join(', ')}, handing work to each other in ${c.steps.length} steps` })] }),
      jsxs('div', { className: 'hk-actions', children: [
        jsx(Button, { onClick: () => go({ name: 'crew', id: c.slug, from: out }), children: out.next.label }),
        site ? jsx(Button, { variant: 'secondary', onClick: () => go({ name: 'setup', id: c.slug }), children: `Set up ${c.name}` }) : null,
        jsx(SiteButton, { href: c.href }),
      ] }),
      m.smaller ? jsx(BotLine, { bot: m.smaller, heading: `Start smaller with ${m.smaller.name} alone`, note: m.smaller.summary }) : null,
    ] })
  }
  const b = m.bot
  return jsxs(Card, { tone: 'info', className: 'hk-enter', children: [
    jsxs('div', { className: 'hk-actions', children: [
      jsx(Button, { onClick: () => nextStep(out), children: out.next.label }),
      out.kind === 'bot' && b && BOT[b.slug] ? jsx(AddButton, { bot: BOT[b.slug], size: null, label: `Add ${b.name} to my bots` }) : null,
    ] }),
    jsx('p', { children: out.next.note }),
    b ? jsx(BotLine, { bot: b, heading: out.kind === 'bot' ? `Closest published Bot: ${b.name}` : `If it grows, ${b.name} can take it on` }) : null,
  ] })
}

function Status({ status, text }) {
  const tone = status === 'passed' ? 'good' : status === 'failed-acceptance' ? 'bad' : 'idle'
  return jsx(Pill, { tone, dot: true, children: text })
}

function nextStep(out) {
  if (out.next.action === 'chat') return sendInNewChat(out.next.draft)
  if (out.next.action === 'lab') {
    $lab.set({ job: out.result, plan: null, kept: {}, fromFit: out.submitted })
    return go({ name: 'lab' })
  }
  if (out.match.crew) return go({ name: 'crew', id: out.match.crew.slug, from: out })
  if (out.next.action === 'crews') return go({ name: 'crews' })
  if (out.match.bot) return go({ name: 'bot', id: out.match.bot.slug })
  return go({ name: 'cabinet' })
}

function Answer({ out }) {
  return jsx(Frame, { back: () => go({ name: 'fit' }), children: jsxs('div', { className: 'hk-split', children: [
    jsxs('div', { className: 'hk-stack', style: { gap: 14 }, children: [
      jsx(Back, { label: 'What do you need done?', onClick: () => go({ name: 'fit' }) }),
      jsxs('header', { className: 'hk-head hk-enter bc-head', children: [
        jsx(Label, { children: 'Your answer' }),
        jsx('h1', { className: 'hk-h1 bc-display', children: out.result }),
        jsxs('div', { className: 'hk-row', children: [
          jsx(Pill, { tone: 'info', icon: KIND_ICON[out.kind], children: out.title }),
          jsx('span', { children: out.meaning }),
        ] }),
        jsxs('p', { className: 'hk-lead', children: [jsx('strong', { children: out.summary }), '. ', out.why] }),
      ] }),
      jsx(Card, { children: jsx(KindScale, { out }) }),
      jsx(MatchCard, { out }),
      out.graduation.length ? jsxs(Card, { tone: 'warn', children: [
        jsx('h2', { className: 'hk-h2', children: 'Before it can run on a schedule' }),
        jsx('ol', { className: 'bc-steps', children: out.graduation.map(s => jsx('li', { children: s }, s)) }),
      ] }) : null,
      jsx(Card, { children: jsx(Facts, { rows: [
        { label: 'First test', value: out.firstTest },
        { label: 'Done when', value: jsx(List, { items: out.doneCheck }) },
        { label: 'Asks you before', value: jsx(List, { items: out.approvals }) },
        { label: 'Learn more', value: jsx('ul', { className: 'bc-ul bc-plain', children: out.learnMore.map(l => jsxs('li', { children: [jsx(Link, { href: l.href, children: l.title }), ` · ${l.by}`] }, l.href)) }) },
      ] }) }),
      jsx(Credit, { credit: out.credit }),
    ] }),
    jsxs('aside', { className: 'hk-stack bc-side', style: { gap: 10 }, children: [
      jsx(WhyPanel, { out }),
    ] }),
  ] }) })
}

/* ------------------------------------------------------------------ Bot Lab -- */

const SOURCE = {
  yours: { text: 'Your words', tone: 'info' },
  standard: { text: 'Suggested', tone: 'idle' },
  placeholder: { text: 'Name it', tone: 'warn' },
}

function LabBanner() {
  return jsxs('header', { className: 'bc-scene hk-enter', children: [
    jsx('img', { className: 'bc-scene-art', src: IMAGES.drafting, alt: '', decoding: 'async', draggable: false }),
    jsx('span', { className: 'bc-hero-scrim', 'aria-hidden': 'true' }),
    jsxs('div', { className: 'bc-scene-copy', children: [
      jsx(Label, { children: 'Bot Lab' }),
      jsx('h1', { className: 'hk-h1 bc-display bc-hero-title', children: 'Plan a new Bot' }),
      jsx('p', { className: 'bc-hero-deck', children: 'Describe its job in a sentence. Cabinet drafts a plan you can change line by line, then makes the Bot' }),
    ] }),
  ] })
}

function BotLab() {
  const lab = useValue($lab)
  const [job, setJob] = useState(lab.job || '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [check, setCheck] = useState(null)
  const [created, setCreated] = useState(null)
  const [kit, setKit] = useState(null)
  const [typedProfile, setTypedProfile] = useState('')
  const plan = lab.plan
  const values = plan ? Object.fromEntries(plan.fields.map(f => [f.id, f.value])) : {}

  useEffect(() => {
    if (lab.job && !lab.plan) start(lab.job)
  }, [])

  // The same plan check as the Add screen: the name is checked against your profiles, every part is checked for
  // safety, and a plan that passes is kept so the profile Hermes makes can be recorded and undone from My Bots.
  useEffect(() => {
    if (!plan) return undefined
    let live = true
    post('lab/plan', typedProfile ? { fields: values, profile: typedProfile } : { fields: values }).then(r => live && setCheck(r), e => live && setError(describeError(e)))
    return () => { live = false }
  }, [plan, typedProfile])

  async function start(text) {
    if (!String(text || '').trim()) return
    setBusy(true)
    setError(null)
    setCreated(null)
    try {
      const body = { job: text }
      const fromFit = lab.fromFit || {}
      for (const k of ['frequency', 'access', 'approvals', 'trigger', 'failurePlan', 'costLimit', 'shutdown']) if (typeof fromFit[k] === 'string' && fromFit[k]) body[k] = fromFit[k]
      const out = await post('lab/start', body)
      $lab.set({ ...lab, job: text, plan: out, kept: {} })
      remember('lab-job', text)
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }

  function edit(id, value) {
    const fields = plan.fields.map(f => (f.id === id ? { ...f, value, source: f.source === 'yours' ? 'yours' : 'edited' } : f))
    $lab.set({ ...lab, plan: { ...plan, fields } })
    setCreated(null)
    setKit(null)
  }

  async function create() {
    if (!check || !check.ready || !check.create) return
    setBusy(true)
    setError(null)
    try {
      // Hermes makes it; Cabinet then checks what was made and records it. If recording fails the profile is
      // still real, and My Bots picks it up the next time it opens.
      await createAndRecord(check.create, check.planId)
      setCreated(check.profile)
      notify('success', `${values.botName} is in your Bots list`)
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }

  async function saveKit() {
    try {
      const out = await post('kits', { fields: values, job: lab.job })
      setKit(out.kit)
      notify('success', `Saved ${out.kit.name} as a kit. Find it in My Bots`)
    } catch (e) {
      setError(describeError(e))
    }
  }

  const canCreate = Boolean(check && check.ready && !check.reserved)
  const closest = plan && plan.closest ? (BOT[plan.closest.slug] || botNamed(plan.closest.name)) : null

  return jsx(Frame, { width: 'narrow', back: () => go({ name: 'start' }), children: [
    jsx(LabBanner, {}, 'head'),
    jsxs(Card, { children: [
      jsx(Field, { label: 'Its job, in a sentence', children: jsx(Textarea, { className: 'bc-job', rows: 2, value: job, 'aria-label': 'Its job, in a sentence',
        placeholder: 'For example, every Monday, summarize what changed on three competitors’ pricing pages',
        onChange: e => setJob(e.target.value), ...submitOnModEnter(() => start(job)) }) }),
      jsxs('div', { className: 'hk-actions', children: [
        jsx(Button, { onClick: () => start(job), loading: busy && !plan, disabled: !job.trim(), children: plan ? 'Start the plan again' : 'Start the plan' }),
        jsx(Button, { variant: 'secondary', disabled: !job.trim(), onClick: () => sendInNewChat(plan ? plan.hermesRequest : `Help me plan a new Bot. The job: ${job.trim()}`), children: 'Ask Hermes to help' }),
        job.trim() ? jsx(KeyHint, { keys: [modKey(), '↵'], label: 'to start' }) : null,
      ] }),
    ] }, 'job'),
    !plan && !busy ? jsxs('div', { className: 'bc-labhint', children: [
      jsx(Label, { children: 'Or start from a published Bot' }),
      jsx('div', { className: 'bc-labpicks', children: BOTS.slice(0, 6).map(b => jsxs('button', { type: 'button', className: 'bc-labpick',
        onClick: () => { setJob(b.outcome); start(b.outcome) }, children: [jsx(Face, { bot: b, size: 30 }), jsxs('span', { children: [jsx('strong', { children: b.name }), jsx('span', { className: 'hk-small', children: b.title })] })] }, b.slug)) }),
    ] }, 'picks') : null,
    busy && !plan ? jsx(SkeletonPage, { header: false, label: 'Drafting the plan', cards: [{ title: true, lines: 6 }] }, 'drafting') : null,
    error ? jsx(Problem, { error }, 'error') : null,
    plan ? jsxs(Card, { className: 'hk-enter', children: [
      jsx('h2', { className: 'hk-h2 bc-serif', children: 'The plan' }),
      closest ? jsx(BotLine, { bot: { ...closest, credit: null }, heading: `Closest published Bot: ${closest.name}`, note: closest.summary }) : null,
      jsx('p', { className: 'hk-small', children: 'Change any line that isn’t right. The Bot is made from what you see here' }),
      jsx('ol', { className: 'bc-plan', children: plan.fields.map(f => {
        const src = SOURCE[f.source] || { text: 'Edited by you', tone: 'info' }
        return jsxs('li', { children: [
          jsxs('div', { className: 'bc-plan-head', children: [
            jsx('span', { className: 'bc-plan-label', children: f.label }),
            jsx(Pill, { tone: src.tone, children: src.text }),
            f.required ? null : jsx('span', { className: 'hk-small', children: 'Optional' }),
          ] }),
          jsx('div', { className: 'hk-small', children: f.purpose }),
          jsx(Textarea, { rows: f.id === 'botName' ? 1 : 2, value: f.value, 'aria-label': f.label, onChange: e => edit(f.id, e.target.value) }),
        ] }, f.id)
      }) }),
    ] }, 'plan') : null,
    plan && check ? jsxs(Card, { tone: 'info', className: 'bc-create', children: [
      jsx('h2', { className: 'hk-h2', children: 'What will be made' }),
      jsx(Facts, { rows: [
        { label: 'A Bot named', value: jsx('strong', { children: values.botName || 'New Bot' }) },
        { label: 'Its model', value: 'Your current model. Change it later in the Bot’s settings' },
        { label: 'It runs', value: 'Only when you message it' },
        check.where ? { label: 'It lives in', value: check.where, mono: true } : null,
      ] }),
      jsx(Field, { label: 'Its profile name', hint: 'Its folder name in Hermes: lowercase letters, numbers and dashes. Cabinet never reuses a name you already have', children: jsx(Input, { className: 'hk-mono',
        value: typedProfile || check.profile || '', 'aria-label': 'Its profile name', onChange: e => setTypedProfile(e.target.value) }) }),
      ...(check.problems || []).map(p => jsx('p', { className: 'bc-error', role: 'alert', children: p.message }, p.field + p.message)),
      check.suggestion ? jsx('div', { className: 'hk-actions', children: jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => setTypedProfile(check.suggestion),
        children: `Use ${check.suggestion}` }) }) : null,
      jsx(Details, { label: 'Instructions it will follow', children: jsx('pre', { className: 'bc-soul', children: check.soul }) }),
      check.missing.length ? jsx('p', { className: 'bc-error', children: ['Still needed: ', check.missing.map(m => m.label).join(', ')] }) : null,
      check.reserved ? jsx('p', { className: 'bc-error', children: 'That name is taken by Hermes. Pick another' }) : null,
      created ? jsxs('div', { className: 'bc-okline hk-enter', role: 'status', children: [Icon('pass-filled'), `${values.botName} is ready. Send it the first test`,
        jsx('button', { type: 'button', className: 'hk-link', onClick: () => openBotChat(created), children: 'Open a chat with it' }),
        jsx('button', { type: 'button', className: 'hk-link', onClick: () => go({ name: 'mybots' }), children: 'See it in My Bots' })] }) : null,
      kit ? jsxs('p', { className: 'bc-okline hk-enter', role: 'status', children: [Icon('pass-filled'), `Saved ${kit.name} as a kit. `,
        jsx('button', { type: 'button', className: 'hk-link', onClick: () => go({ name: 'mybots' }), children: 'See it in My Bots' })] }) : null,
      jsxs('div', { className: 'hk-actions', children: [
        jsx(Button, { onClick: create, loading: busy && Boolean(plan), disabled: !canCreate || Boolean(created), children: `Create ${values.botName && values.botName !== 'New Bot' ? values.botName : 'the Bot'}` }),
        jsx(Button, { variant: 'secondary', onClick: saveKit, disabled: !values.botName || values.botName === 'New Bot' || Boolean(kit), children: 'Save as a kit' }),
      ] }),
      jsx('p', { className: 'hk-small', children: 'Save as a kit keeps the plan in My Bots, so you can make more bots from it later' }),
      jsx('div', { className: 'bc-plain-links', children: plan.learnMore.map(l => jsxs('span', { children: [jsx(Link, { href: l.href, children: l.title }), ` · ${l.by}`] }, l.href)) }),
    ] }, 'create') : null,
  ] })
}

/* ------------------------------------------------------------------ review screens -- */

function Checkbox({ checked, onChange, disabled, children }) {
  return jsxs('label', { className: 'bc-check', children: [
    jsx('input', { type: 'checkbox', checked: Boolean(checked), disabled, onChange: e => onChange(e.target.checked) }),
    jsx('span', { children }),
  ] })
}

function Problems({ list, fields }) {
  const shown = (list || []).filter(p => !fields || fields.includes(p.field))
  return shown.length ? jsx('div', { className: 'bc-problems', children: shown.map(p => jsx('p', { className: 'bc-error', role: 'alert', children: p.message }, p.field + p.message)) }) : null
}

/** A stable text key for a plain object, so an effect re-runs when an edit changes it. */
function keyOf(v) {
  if (v && typeof v === 'object') return `{${Object.keys(v).sort().map(k => `${k}:${keyOf(v[k])}`).join(',')}}`
  return String(v)
}

/** A plan from the backend for what's on screen, re-checked a moment after each edit. Nothing is written by asking. */
function usePlan(path, body) {
  const [state, setState] = useState({ plan: null, error: null, loading: true })
  const [nonce, setNonce] = useState(0)
  const key = keyOf(body)
  useEffect(() => {
    let live = true
    if (!path) { setState({ plan: null, error: null, loading: false }); return () => {} }
    const timer = setTimeout(() => {
      post(path, body).then(
        plan => live && setState({ plan, error: null, loading: false }),
        e => live && setState(s => ({ plan: s.plan, error: describeError(e), loading: false })))
    }, state.plan ? 320 : 0)
    return () => { live = false; clearTimeout(timer) }
  }, [path, key, nonce])
  return { ...state, replan: () => setNonce(n => n + 1) }
}

/** Remove, with a preview of exactly what goes and what stays. Only things Cabinet made and you haven't changed. */
function RemoveBox({ kind, id, name, onDone, onCancel }) {
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    rest(`remove/${kind}/${id}`).then(setPreview, e => setError(describeError(e)))
  }, [kind, id])
  async function remove() {
    setBusy(true)
    try {
      const out = await post(`remove/${kind}/${id}`, { confirm: true })
      notify('success', `${name} removed`)
      onDone(out)
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }
  const kept = preview ? preview.keep || [] : []
  return jsxs(Card, { tone: 'warn', className: 'bc-removebox hk-enter', children: [
    jsx('h3', { className: 'hk-h3', children: `Remove ${name}` }),
    !preview && !error ? jsx('p', { className: 'hk-small', children: 'Checking what Cabinet may remove' }) : null,
    preview ? jsx('p', { children: preview.note }) : null,
    preview && preview.will.length ? jsxs('div', { children: [jsx('strong', { children: 'Cabinet will remove' }), jsx(List, { items: preview.will })] }) : null,
    kept.length ? jsxs('div', { children: [jsx('strong', { children: 'These stay, because you have changed them' }),
      jsx('ul', { className: 'bc-ul', children: kept.map(k => jsxs('li', { children: [jsx('span', { className: 'hk-mono', children: k.profile }), Array.isArray(k.why) && k.why.length > 1 ? jsx(List, { items: k.why }) : `: ${lowerFirst(Array.isArray(k.why) ? k.why[0] || '' : String(k.why))}`] }, k.profile)) })] }) : null,
    preview && preview.reused && preview.reused.length ? jsxs('div', { children: [jsx('strong', { children: 'Yours already, never touched' }), jsx(List, { items: preview.reused })] }) : null,
    preview && preview.cards ? jsx('p', { className: 'hk-small', children: `${preview.cards} Kanban ${preview.cards === 1 ? 'card' : 'cards'} made for this crew will be archived` }) : null,
    preview && !preview.canRemove ? jsx('p', { className: 'hk-small', children: 'Cabinet will not remove this one. If you are sure, delete it in Hermes yourself' }) : null,
    error ? jsx('p', { className: 'bc-error', role: 'alert', children: String(error.detail || 'That didn’t work') }) : null,
    jsxs('div', { className: 'hk-actions', children: [
      preview && preview.canRemove ? jsx(Button, { onClick: remove, loading: busy, children: 'Remove it' }) : null,
      jsx(Button, { variant: 'secondary', onClick: onCancel, children: preview && preview.canRemove ? 'Keep it' : 'Close' }),
    ] }),
  ] })
}

/* ------------------------------------------------------------------ adding a Bot -- */

function AddBot({ id }) {
  const bot = BOT[id]
  const back = () => go({ name: 'bot', id })
  const [edits, setEdits] = useState({})
  const [phase, setPhase] = useState('review')
  const [done, setDone] = useState(null)
  const [error, setError] = useState(null)
  const [undo, setUndo] = useState(false)
  const catalogState = useValue($catalog)
  const hb = useValue($hermesBots)
  // A Bot Hermes's own catalog carries, or already added, is not planned here: no second copy, no Cabinet record.
  const added = addedBy(catalogState, bot)
  const carried = carriedBy(hb, bot)
  const handOver = phase !== 'done' && Boolean(added || carried)
  const { plan, error: planError, loading, replan } = usePlan(bot && !handOver ? `bots/${id}/plan` : null, { edits })
  if (!bot) {
    return jsx(Frame, { back: () => go({ name: 'cabinet' }), children: jsx(Empty, { icon: 'robot', title: 'That Bot isn’t in this copy of The Cabinet',
      action: jsx(Button, { variant: 'secondary', onClick: () => go({ name: 'cabinet' }), children: 'Back to The Cabinet' }) }) })
  }
  if (handOver) {
    return jsx(Frame, { back, children: [
      jsx(Back, { label: bot.name, onClick: back }, 'back'),
      jsxs('header', { className: 'hk-head hk-enter bc-head bc-addhead', children: [
        jsx(Face, { bot, size: 64, ring: true }),
        jsxs('div', { className: 'hk-stack', style: { gap: 4 }, children: [
          jsx(Label, { children: added ? 'Already in your bots' : 'Add in Hermes Bots' }),
          jsx('h1', { className: 'hk-h1 bc-display', children: added ? `${bot.name} is already added` : `Add ${bot.name} in Hermes Bots` }),
          jsx('p', { className: 'hk-lead', children: added ? 'Hermes’s own Bots catalog already added it. Open a chat with it from your Bots list'
            : 'Hermes’s own Bots catalog carries this Bot, so you add it there and Cabinet makes no copy of its own' }),
        ] }),
      ] }, 'head'),
      added ? jsxs(Card, { tone: 'good', className: 'bc-panel', children: [
        jsxs('p', { className: 'bc-okline', role: 'status', children: [Icon('pass-filled'), 'Already added through Hermes Bots'] }),
        jsx(Facts, { rows: [
          { label: 'Its profile', value: added, mono: true },
          { label: 'Added by', value: 'Hermes Bots. Cabinet doesn’t record it, change it or remove it' },
        ] }),
        jsxs('div', { className: 'hk-actions', children: [
          jsxs(Button, { onClick: () => openBotChat(added), children: [Icon('comment', '', 13), `Open a chat with ${bot.name}`] }),
          jsx(Button, { variant: 'secondary', onClick: back, children: `Back to ${bot.name}` }),
        ] }),
      ] }, 'added') : jsxs(Card, { tone: 'info', className: 'bc-panel', children: [
        jsxs('ol', { className: 'bc-steps', children: [
          jsx('li', { children: 'Press Add in Hermes Bots. It opens Capabilities, in the Bots tab' }),
          jsx('li', { children: `Pick “${carried}”. That is the name Hermes lists ${bot.name} under` }),
          jsx('li', { children: 'Review it there, then add it' }),
        ] }),
        jsxs('div', { className: 'hk-actions', children: [
          jsxs(Button, { onClick: () => openHermesBots(bot, carried), children: [Icon('arrow-right', '', 13), 'Add in Hermes Bots'] }),
          jsx(Button, { variant: 'secondary', onClick: back, children: 'Cancel' }),
        ] }),
        jsx('p', { className: 'hk-small', children: 'Cabinet still tells you what this Bot does and what has been tested. Its page here stays the same' }),
      ] }, 'carried'),
      jsx(StaleLine, {}, 'stale'),
    ] })
  }
  const val = k => (edits[k] !== undefined ? edits[k] : plan ? plan[k] : '')
  const set = (k, v) => setEdits({ ...edits, [k]: v })
  const builtin = edits.noSkills !== undefined ? !edits.noSkills : plan ? !plan.noSkills : false

  async function add() {
    if (!plan || !plan.ready || !plan.create) return
    setPhase('making')
    setError(null)
    let made
    try {
      made = await createAndRecord(plan.create, plan.planId)
    } catch (e) {
      setError(describeError(e))
      setPhase('review')
      replan()
      return
    }
    setDone(made.recorded ? { install: made.out.install, recorded: true }
      : { install: { profile: plan.profile, displayName: plan.displayName, where: plan.where }, recorded: false, error: made.error })
    setPhase('done')
    notify('success', `${plan.displayName} is in your bots`)
  }

  if (phase === 'done' && done) {
    const inst = done.install
    return jsx(Frame, { back: () => go({ name: 'mybots' }), children: [
      jsxs('header', { className: 'hk-head hk-enter bc-head bc-addhead', children: [
        jsx(Face, { bot, size: 64, ring: true }),
        jsxs('div', { className: 'hk-stack', style: { gap: 4 }, children: [
          jsx(Label, { children: 'Added' }),
          jsx('h1', { className: 'hk-h1 bc-display', children: `${inst.displayName || bot.name} is in your bots` }),
          jsx('p', { className: 'hk-lead', children: 'It is a normal Hermes bot now. Open a chat with it, or find it in the Bots tab' }),
        ] }),
      ] }, 'head'),
      done.recorded ? null : jsx(Notice, { tone: 'warn', icon: 'warning', title: 'Hermes made it, but Cabinet couldn’t record it yet',
        children: 'It will appear in My Bots the next time you open that page, with its Remove button. Nothing was lost' }, 'unrecorded'),
      jsxs(Card, { tone: 'good', className: 'bc-panel', children: [
        jsx(Facts, { rows: [
          { label: 'Its profile', value: inst.profile, mono: true },
          { label: 'It lives in', value: inst.where, mono: true },
          { label: 'Made from', value: `${bot.name} on botcabinet.com` },
        ] }),
        jsxs('div', { className: 'hk-actions', children: [
          jsxs(Button, { onClick: () => openBotChat(inst.profile), children: [Icon('comment', '', 13), `Open a chat with ${inst.displayName || bot.name}`] }),
          jsx(Button, { variant: 'secondary', onClick: () => go({ name: 'mybots' }), children: 'See My Bots' }),
          done.recorded ? jsx(Button, { variant: 'secondary', onClick: () => setUndo(true), children: 'Undo this' }) : null,
        ] }),
      ] }, 'done'),
      undo && inst.id ? jsx(RemoveBox, { kind: 'bot', id: inst.id, name: inst.displayName || bot.name, onCancel: () => setUndo(false), onDone: () => go({ name: 'mybots' }) }, 'undo') : null,
      jsxs(Card, { className: 'bc-panel', children: [
        jsx('h2', { className: 'hk-h2 bc-serif', children: 'Try its first test' }),
        jsx(Steps, { text: bot.firstMission }),
        jsx('p', { className: 'hk-small', children: 'It does nothing on a schedule. Run the first test by hand, and set up a routine in Hermes only once you trust the result' }),
      ] }, 'first'),
    ] })
  }

  const problemsFor = f => (plan ? plan.problems.filter(p => p.field === f) : [])
  return jsx(Frame, { back, children: [
    jsx(Back, { label: bot.name, onClick: back }, 'back'),
    jsxs('header', { className: 'hk-head hk-enter bc-head bc-addhead', children: [
      jsx(Face, { bot, size: 64, ring: true }),
      jsxs('div', { className: 'hk-stack', style: { gap: 4 }, children: [
        jsx(Label, { children: 'Add to my bots' }),
        jsx('h1', { className: 'hk-h1 bc-display', children: `Add ${bot.name}` }),
        jsx('p', { className: 'hk-lead', children: 'Check the details and change anything you like. Nothing is made until you press Add' }),
      ] }),
    ] }, 'head'),
    jsx(HandoverNotice, { bot }, 'handover'),
    planError ? jsx(Problem, { error: planError, retry: replan }, 'planerror') : null,
    error ? jsx(Problem, { error, retry: () => setError(null) }, 'error') : null,
    !plan && loading ? jsx(SkeletonPage, { header: false, label: 'Preparing the review', cards: [{ title: true, lines: 4 }, { title: true, lines: 8 }] }, 'loading') : null,
    plan ? jsxs('div', { className: 'bc-addgrid', children: [
      jsxs('div', { className: 'hk-stack', style: { gap: 14 }, children: [
        jsxs(Card, { className: 'bc-panel', children: [
          jsx('h2', { className: 'hk-h2 bc-serif', children: 'Name and place' }),
          plan.nameNote && edits.profile === undefined ? jsx(Notice, { tone: 'info', icon: 'info', title: 'You already have a bot with that name', children: plan.nameNote }) : null,
          jsx(Field, { label: 'Name', hint: 'What you will call it in your Bots list', children: jsx(Input, { value: val('displayName'), 'aria-label': 'Name', onChange: e => set('displayName', e.target.value) }) }),
          jsx(Problems, { list: plan.problems, fields: ['displayName'] }),
          jsx(Field, { label: 'Profile name', hint: 'Its folder name in Hermes: lowercase letters, numbers and dashes. Cabinet never reuses a name you already have',
            children: jsx(Input, { className: 'hk-mono', value: val('profile'), 'aria-label': 'Profile name', onChange: e => set('profile', e.target.value) }) }),
          jsx(Problems, { list: plan.problems, fields: ['profile'] }),
          plan.suggestion && problemsFor('profile').length ? jsx('div', { className: 'hk-actions', children: jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => set('profile', plan.suggestion),
            children: `Use ${plan.suggestion}` }) }) : null,
          jsx(Field, { label: 'Description', hint: 'One line that appears under its name', children: jsx(Input, { value: val('description'), 'aria-label': 'Description', onChange: e => set('description', e.target.value) }) }),
          jsx(Problems, { list: plan.problems, fields: ['description'] }),
        ] }, 'name'),
        jsxs(Card, { className: 'bc-panel', children: [
          jsx('h2', { className: 'hk-h2 bc-serif', children: 'Instructions it will follow' }),
          jsx('p', { className: 'hk-small', children: 'Its role, what it is given and hands back, where it asks you first, and its limits. Rewrite anything you like' }),
          jsx(Textarea, { className: 'bc-soul-edit', rows: 16, value: val('soul'), 'aria-label': 'Instructions', onChange: e => set('soul', e.target.value) }),
          jsx(Problems, { list: plan.problems, fields: ['soul'] }),
          edits.soul !== undefined ? jsx('div', { className: 'hk-actions', children: jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => { const { soul, ...rest } = edits; setEdits(rest) },
            children: 'Use the catalog’s wording again' }) }) : null,
        ] }, 'soul'),
      ] }),
      jsxs('aside', { className: 'bc-add-side hk-stack', style: { gap: 12 }, children: [
        jsxs(Card, { tone: 'info', className: 'bc-panel', children: [
          jsx('h2', { className: 'hk-h2', children: 'What will be made' }),
          jsx(Facts, { rows: [
            { label: 'Where it lives', value: plan.where || 'Not set yet', mono: true },
            { label: 'Skills', value: plan.skills.length ? `${plan.skills.join(', ')}, written into its instructions` : plan.skillsNote },
            { label: 'Tools', value: plan.toolsNote },
            { label: 'Model', value: plan.modelNote },
            { label: 'Schedule', value: 'None set. Cabinet never turns on a schedule' },
            plan.schedule.suggested ? { label: 'Its page says', value: plan.schedule.suggested } : null,
            { label: 'Sign-in', value: plan.signInNote },
            plan.source.riskLevel ? { label: 'Risk level', value: `${plan.source.riskLevel}, as published` } : null,
          ] }),
          jsx(Checkbox, { checked: builtin, onChange: on => set('noSkills', !on), children: 'Also give it Hermes’s built-in skills' }),
          bot.credits.length ? jsx('div', { className: 'bc-credits', children: bot.credits.map(c => jsx(Credit, { credit: c }, c.href)) }) : null,
          !plan.ready ? jsx('p', { className: 'hk-small', children: 'Fix the points above, then Add is ready' }) : null,
          jsxs('div', { className: 'hk-actions', children: [
            jsx(Button, { onClick: add, loading: phase === 'making', disabled: !plan.ready || phase === 'making' || loading,
              children: `Add ${val('displayName') || bot.name}` }),
            jsx(Button, { variant: 'secondary', onClick: back, children: 'Cancel' }),
          ] }),
          jsx('p', { className: 'hk-small', children: 'Remove it any time from My Bots, as long as you have not changed it' }),
        ] }, 'review'),
      ] }),
    ] }, 'add') : null,
    jsx(StaleLine, {}, 'stale'),
  ] })
}

/* ------------------------------------------------------------------ setting up a crew -- */

/** Hermes can make the bots and the cards. It cannot run the crew by itself. One line says so; the detail folds away. */
function HonestCrew({ honest }) {
  return jsxs('div', { className: 'bc-honest', children: [
    jsxs('p', { className: 'hk-small', children: [Icon('info', '', 12), ' Hermes doesn’t follow a crew’s order by itself. You move work along with Kanban cards, or talk it through with the bots in a group chat'] }),
    jsxs(Details, { label: 'What Cabinet does and doesn’t do for a crew', children: [
      jsxs('div', { className: 'bc-two', children: [
        jsxs('div', { className: 'hk-stack', style: { gap: 6 }, children: [jsxs('strong', { children: [Icon('pass-filled', 'bc-check-icon', 13), ' Cabinet can'] }), jsx(List, { items: honest.can })] }),
        jsxs('div', { className: 'hk-stack', style: { gap: 6 }, children: [jsxs('strong', { children: [Icon('circle-slash', 'bc-check-icon', 13), ' Hermes does not do this for you'] }), jsx(List, { items: honest.cannot })] }),
      ] }),
      honest.groupChat ? jsxs('p', { className: 'hk-small', children: [Icon('organization', '', 12), ' ', honest.groupChat] }) : null,
    ] }),
  ] })
}

function StepOrder({ plan }) {
  return jsx('ol', { className: 'bc-steplist', children: plan.steps.map(s => jsxs('li', { 'data-you': s.you ? '' : undefined, children: [
    jsx('span', { className: 'bc-steplist-n', children: String(s.n) }),
    jsxs('span', { className: 'bc-steplist-copy', children: [
      jsxs('strong', { children: [s.step, ' · ', jsx('span', { className: 'bc-steplist-owner', children: s.owner })] }),
      jsx('span', { children: s.action }),
      s.you ? jsxs('span', { className: 'bc-stepin', children: [Icon('person', '', 11), 'It stops here and waits for you'] }) : null,
    ] }),
  ] }, s.n)) })
}

function CardsPanel({ crewId, onChange }) {
  const [plan, setPlan] = useState(null)
  const [error, setError] = useState(null)
  const [sure, setSure] = useState(false)
  const [busy, setBusy] = useState(false)
  useEffect(() => { rest(`crews/${crewId}/cards`).then(setPlan, e => setError(describeError(e))) }, [crewId])
  async function make() {
    setBusy(true)
    try {
      await post(`crews/${crewId}/cards`, { confirm: true })
      notify('success', 'Kanban cards made. They start blocked')
      const next = await rest(`crews/${crewId}/cards`)
      setPlan(next)
      if (onChange) onChange()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }
  return jsxs(Card, { className: 'bc-cardsplan hk-enter', children: [
    jsx('h3', { className: 'hk-h3', children: 'Kanban cards for this crew' }),
    error ? jsx('p', { className: 'bc-error', role: 'alert', children: String(error.detail || 'That didn’t work') }) : null,
    plan ? jsxs(Fragment, { children: [
      jsx('p', { children: plan.note }),
      jsx('ol', { className: 'bc-cardlist', children: plan.cards.map(c => jsxs('li', { children: [
        jsx('strong', { children: c.title }),
        jsx('span', { className: 'hk-small', children: c.assignee ? ` · assigned to ${c.assignee}` : ' · for you' }),
      ] }, c.n)) }),
      plan.created ? jsxs('p', { className: 'bc-okline', role: 'status', children: [Icon('pass-filled'), `${plan.cardIds.length} cards are made, blocked until you release the first`] })
        : !plan.available ? jsx('p', { className: 'hk-small', children: 'This Hermes has no Kanban board Cabinet can reach, so no cards can be made' })
          : jsxs(Fragment, { children: [
            jsx(Checkbox, { checked: sure, onChange: setSure, children: `Yes, make these ${plan.cards.length} cards. They start blocked, and nothing runs until I release the first one` }),
            jsx('div', { className: 'hk-actions', children: jsx(Button, { onClick: make, loading: busy, disabled: !sure, children: `Make ${plan.cards.length} Kanban cards` }) }),
          ] }),
    ] }) : null,
  ] })
}

/**
 * After a crew is set up: Hermes's own group chats put its bots in one room (hermes-bots group-chat.ts). One message
 * from you gets a few short turns from them, in no set order; the crew's ordered steps stay with the Kanban cards.
 */
function GroupChatTip({ crew }) {
  const names = crew.members.filter(m => m.mode !== 'missing').map(m => m.name)
  if (names.length < 2) return null
  const list = names.length === 2 ? names.join(' and ') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
  return jsxs(Card, { className: 'bc-panel', children: [
    jsxs('h2', { className: 'hk-h2 bc-serif', children: [Icon('organization', '', 16), ' Start a group chat with these bots'] }),
    jsx('p', { children: 'To talk the job through with all of them at once:' }),
    jsxs('ol', { className: 'bc-steps', children: [
      jsx('li', { children: 'Open the + menu next to Bots in the sidebar' }),
      jsx('li', { children: 'Choose New group chat' }),
      jsx('li', { children: `Add ${list}` }),
    ] }),
    jsx('p', { children: 'Every bot in the room hears the conversation, and you can @mention one by name' }),
    jsx('p', { className: 'hk-small', children: 'A group chat has no set order and doesn’t stop for you at a step. For the crew’s order, use the Kanban cards below' }),
  ] })
}

function SetUpCrew({ id }) {
  const crew = CREW[id]
  const back = () => go({ name: 'crew', id })
  const [choices, setChoices] = useState({})
  const [phase, setPhase] = useState('review')
  const [progress, setProgress] = useState({})
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [undo, setUndo] = useState(false)
  const { plan, error: planError, loading, replan } = usePlan(`crews/${id}/setup`, { choices })
  if (!crew) {
    return jsx(Frame, { back: () => go({ name: 'crews' }), children: jsx(Empty, { icon: 'organization', title: 'That crew isn’t in this copy of Cabinet',
      action: jsx(Button, { variant: 'secondary', onClick: () => go({ name: 'crews' }), children: 'Back to the Crew Kits' }) }) })
  }
  const pick = (slug, patch) => setChoices({ ...choices, [slug]: { ...(choices[slug] || {}), ...patch } })

  async function setUp() {
    if (!plan || !plan.ready) return
    setPhase('making')
    setError(null)
    const created = []
    const state = {}
    for (const m of plan.members) {
      if (m.mode !== 'create') { state[m.slug] = 'reused'; continue }
      state[m.slug] = 'making'
      setProgress({ ...state })
      try {
        await createProfile(m.create)
        created.push(m.profile)
        state[m.slug] = 'made'
      } catch (e) {
        // Cabinet's backend still finds this bot if Hermes finished making it after the reply was lost.
        state[m.slug] = 'failed'
        setError(describeError(e))
        break
      }
      setProgress({ ...state })
    }
    setProgress({ ...state })
    try {
      const out = await post('crews/confirm', { planId: plan.planId, created })
      setResult(out)
    } catch (e) {
      setError(describeError(e))
      setResult({ ok: false, crew: null, missing: [], unrecorded: true })
    }
    setPhase('done')
  }

  const header = (label, title, lead) => jsxs('header', { className: 'hk-head hk-enter bc-head', children: [
    jsx(Label, { children: label }),
    jsx('h1', { className: 'hk-h1 bc-display', children: title }),
    jsx('p', { className: 'hk-lead', children: lead }),
  ] }, 'head')

  if (phase !== 'review') {
    const done = phase === 'done'
    const view = result && result.crew
    return jsx(Frame, { back: done ? () => go({ name: 'mybots' }) : undefined, children: [
      header(done ? 'Crew set up' : 'Setting up', done ? (result && result.ok ? `${crew.name} is set up` : `${crew.name} is not finished`) : `Setting up ${crew.name}`,
        done && result && result.ok ? 'Each bot is a Hermes profile now. The crew’s order and its stops for you are recorded in My Bots'
          : done ? 'Some bots weren’t made. What was made is recorded, and you can finish from here' : 'Making each bot, one at a time. Nothing else is touched'),
      jsxs(Card, { className: 'bc-panel', children: [
        jsx('ul', { className: 'bc-progress', children: plan.members.map(m => {
          const st = progress[m.slug] || 'waiting'
          const icon = { waiting: 'circle-large-outline', making: 'sync', made: 'pass-filled', reused: 'link', failed: 'error' }[st]
          const text = { waiting: 'Waiting', making: 'Making it', made: `Made as ${m.profile}`, reused: `Using your ${m.profile}`, failed: 'Hermes couldn’t make it' }[st]
          return jsxs('li', { 'data-state': st, children: [Icon(icon, 'bc-progress-icon', 15), jsx(Face, { bot: BOT[m.slug], size: 30 }), jsx('strong', { children: m.name }), jsx('span', { className: 'hk-small', children: text })] }, m.slug)
        }) }),
        error ? jsx(Problem, { error }) : null,
        result && result.unrecorded ? jsx(Notice, { tone: 'warn', icon: 'warning', title: 'Cabinet couldn’t record the crew', children: 'The bots Hermes made are real. Look in the Bots tab, then try setting up the crew again to record it' }) : null,
        done ? jsxs('div', { className: 'hk-actions', children: [
          result && !result.ok && !result.unrecorded ? jsx(Button, { onClick: () => { setPhase('review'); setResult(null); setProgress({}); replan() }, children: 'Finish setting it up' }) : null,
          jsx(Button, { variant: result && result.ok ? 'default' : 'secondary', onClick: () => go({ name: 'mybots' }), children: 'See My Bots' }),
          view ? jsx(Button, { variant: 'secondary', onClick: () => setUndo(true), children: 'Undo this' }) : null,
        ] }) : null,
      ] }, 'progress'),
      undo && view ? jsx(RemoveBox, { kind: 'crew', id: view.id, name: crew.name, onCancel: () => setUndo(false), onDone: () => go({ name: 'mybots' }) }, 'undo') : null,
      done && view && view.status === 'active' ? jsx(GroupChatTip, { crew: view }, 'groupchat') : null,
      done && view && view.status === 'active' ? jsx(CardsPanel, { crewId: view.id }, 'cards') : null,
    ] })
  }

  return jsx(Frame, { back, children: [
    jsx(Back, { label: crew.name, onClick: back }, 'back'),
    header('Set up a crew', `Set up ${crew.name}`, 'Cabinet makes each bot below with its part in the crew written into its instructions. Nothing is made until you press the button at the bottom'),
    jsx(HandoverNotice, {}, 'handover'),
    jsx(FailedCrewNotice, { crew }, 'failed'),
    planError ? jsx(Problem, { error: planError, retry: replan }, 'planerror') : null,
    !plan && loading ? jsx(SkeletonPage, { header: false, label: 'Preparing the review', cards: [{ title: true, lines: 4 }, { title: true, lines: 8 }] }, 'loading') : null,
    plan ? jsxs(Fragment, { children: [
      jsxs(Card, { className: 'bc-panel', children: [
        jsx('h2', { className: 'hk-h2 bc-serif', children: 'Who does what, in order' }),
        jsx('p', { className: 'hk-small', children: 'Work passes from the top down. A marked step is where the crew stops and waits for you' }),
        jsx(StepOrder, { plan }),
      ] }, 'order'),
      jsxs(Card, { className: 'bc-panel', children: [
        jsx('h2', { className: 'hk-h2 bc-serif', children: `The ${plan.members.length} bots` }),
        jsx('p', { className: 'hk-small', children: `${plan.willCreate} will be made new${plan.willReuse ? ` and ${plan.willReuse} of yours will be used as they are` : ''}. Cabinet never changes a profile you already have` }),
        jsx('ul', { className: 'bc-crewbots', children: plan.members.map(m => {
          const mine = m.mode === 'existing'
          const chosen = (choices[m.slug] || {}).profile
          return jsxs('li', { className: 'bc-crewbot', 'data-mode': m.mode, children: [
            jsxs('div', { className: 'bc-crewbot-head', children: [
              jsx(Face, { bot: BOT[m.slug], size: 44 }),
              jsxs('div', { className: 'hk-stack', style: { gap: 2 }, children: [jsx('strong', { children: m.name }), jsx('span', { className: 'bc-member-job', children: m.responsibility }),
                m.resume ? jsx(Pill, { tone: 'info', children: 'Made by Cabinet earlier. It carries over' }) : null,
                m.hermesBots && m.hermesBots.profile ? jsx(Pill, { tone: 'info', children: 'From Hermes Bots' }) : null] }),
              (m.candidates.length || mine) && !m.resume && !m.hermesBots ? jsx(SegmentedControl, { className: 'bc-seg', value: m.mode, options: [{ id: 'create', label: 'Make a new bot' }, { id: 'existing', label: 'Use one I have' }],
                onChange: mode => pick(m.slug, { mode, profile: undefined }) }) : null,
            ] }),
            m.hermesBots && !m.hermesBots.profile ? jsxs('div', { className: 'bc-crewbot-body', children: [
              jsx('p', { className: 'hk-small', children: `${m.name} comes from Hermes’s own Bots catalog. Add it there, then check again and the crew uses it` }),
              jsxs('div', { className: 'hk-actions', children: [
                jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => openHermesBots(BOT[m.slug] || { name: m.name, slug: m.slug }, m.name), children: 'Open Hermes Bots' }),
                jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => { probeHermesBots(true); replan() }, children: 'Check again' }),
              ] }),
            ] }) : mine ? jsxs('div', { className: 'bc-crewbot-body', children: [
              jsx('p', { className: 'hk-small', children: 'Cabinet will use this profile exactly as it is and never writes into it. Copy its part in the crew and paste it into the bot’s instructions in Hermes yourself' }),
              m.brief ? jsxs('div', { className: 'hk-actions', children: [
                jsxs(Button, { variant: 'secondary', size: 'sm', onClick: () => copyText(m.brief, `Copied ${m.name}’s part in the crew`), children: [Icon('copy', '', 12), 'Copy its part in the crew'] }),
                jsx(Details, { label: 'What it says', children: jsx('pre', { className: 'bc-soul', children: m.brief }) }),
              ] }) : null,
              jsx('div', { className: 'bc-chips', role: 'group', 'aria-label': `Which of your bots is ${m.name}`, children: (plan.existingProfiles || []).map(p => jsx('button', { type: 'button', className: 'bc-chip',
                'aria-pressed': (chosen || m.profile) === p, onClick: () => pick(m.slug, { mode: 'existing', profile: p }), children: p }, p)) }),
            ] }) : jsxs('div', { className: 'bc-crewbot-body', children: [
              jsx(Field, { label: 'Profile name', hint: m.where ? `It will live in ${m.where}` : undefined, children: jsx(Input, { className: 'hk-mono', value: chosen !== undefined ? chosen : m.profile,
                'aria-label': `Profile name for ${m.name}`, onChange: e => pick(m.slug, { mode: 'create', profile: e.target.value }) }) }),
              jsx(Details, { label: 'Instructions it will follow', children: jsx('pre', { className: 'bc-soul', children: m.soul }) }),
            ] }),
            jsx(Problems, { list: m.problems }),
          ] }, m.slug)
        }) }),
      ] }, 'bots'),
      jsxs('div', { className: 'bc-two', children: [
        jsxs(Card, { className: 'bc-panel', children: [jsx('h2', { className: 'hk-h2', children: 'It asks you before' }), jsx(List, { items: plan.approvals })] }),
        jsxs(Card, { className: 'bc-panel', children: [jsx('h2', { className: 'hk-h2', children: 'Off limits' }), jsx(List, { items: plan.offLimits })] }),
      ] }, 'limits'),
      jsx(HonestCrew, { honest: plan.honest }, 'honest'),
      jsxs(Card, { tone: 'info', className: 'bc-panel bc-create', children: [
        jsx(Facts, { rows: [
          { label: 'Will make', value: `${plan.willCreate} new ${plan.willCreate === 1 ? 'bot' : 'bots'}` },
          { label: 'Will use as they are', value: plan.willReuse ? `${plan.willReuse} of your own` : 'None' },
          { label: 'Models, tools, schedules', value: 'Not set. Hermes gives each new bot your current model, and Cabinet turns on no schedule' },
        ] }),
        !plan.ready ? jsx('p', { className: 'hk-small', children: 'Fix the points above, then the button is ready' }) : null,
        jsxs('div', { className: 'hk-actions', children: [
          jsx(Button, { onClick: setUp, disabled: !plan.ready || loading, children: `Set up ${crew.name}` }),
          jsx(Button, { variant: 'secondary', onClick: back, children: 'Cancel' }),
        ] }),
      ] }, 'go'),
    ] }) : null,
    jsx(StaleLine, {}, 'stale'),
  ] })
}

/* ------------------------------------------------------------------ My Bots -- */

const STATUS_TONE = { ready: 'good', changed: 'warn', missing: 'bad', removed: 'idle', released: 'idle' }

function KitMaker({ kit, onDone, onCancel }) {
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const { plan, error: planError } = usePlan(`kits/${kit.id}/plan`, typed ? { profile: typed } : {})
  async function make() {
    if (!plan || !plan.ready || !plan.create) return
    setBusy(true)
    setError(null)
    try {
      await createAndRecord(plan.create, plan.planId) // if recording fails, My Bots records it the next time it opens
      notify('success', `${plan.displayName} is in your bots`)
      onDone()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }
  return jsxs(Card, { tone: 'info', className: 'bc-removebox hk-enter', children: [
    jsx('h3', { className: 'hk-h3', children: `Make a bot from ${kit.name}` }),
    planError ? jsx('p', { className: 'bc-error', role: 'alert', children: String(planError.detail) }) : null,
    plan ? jsxs(Fragment, { children: [
      jsx(Field, { label: 'Profile name', hint: plan.where ? `It will live in ${plan.where}` : undefined, children: jsx(Input, { className: 'hk-mono', value: typed || plan.profile,
        'aria-label': 'Profile name', onChange: e => setTyped(e.target.value) }) }),
      jsx(Problems, { list: plan.problems }),
      plan.suggestion ? jsx('div', { className: 'hk-actions', children: jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => setTyped(plan.suggestion), children: `Use ${plan.suggestion}` }) }) : null,
    ] }) : null,
    error ? jsx('p', { className: 'bc-error', role: 'alert', children: String(error.detail) }) : null,
    jsxs('div', { className: 'hk-actions', children: [
      jsx(Button, { onClick: make, loading: busy, disabled: !plan || !plan.ready, children: 'Make it' }),
      jsx(Button, { variant: 'secondary', onClick: onCancel, children: 'Cancel' }),
    ] }),
  ] })
}

function MyBots() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(null) // { kind: 'remove' | 'cards' | 'kit', ... }
  const load = () => rest('mybots').then(d => { setData(d); setError(null) }, e => setError(describeError(e)))
  useEffect(() => { load() }, [])
  const mine = data || { bots: [], crews: [], kits: [], history: [], hermesBots: [], where: '' }
  const viaHermes = mine.hermesBots || []
  const nothing = data && !data.bots.length && !data.crews.length && !data.kits.length && !viaHermes.length
  const faceFor = slug => (slug ? BOT[slug] : null)
  const after = () => { setOpen(null); load() }
  // A confirm or panel opens inside the card it belongs to, next to the button that opened it.
  const panelFor = (rk, id) => {
    if (!open || open.id !== id) return null
    if (open.kind === 'remove' && open.rk === rk) return jsx(RemoveBox, { kind: open.rk, id: open.id, name: open.name, onCancel: () => setOpen(null), onDone: after }, 'remove')
    if (open.kind === 'cards' && rk === 'crew') return jsx(CardsPanel, { crewId: open.id, onChange: load }, 'cards')
    if (open.kind === 'kit' && rk === 'kit') return jsx(KitMaker, { kit: open.kit, onCancel: () => setOpen(null), onDone: after }, 'kit')
    return null
  }
  const some = data && !nothing
  return jsx(Frame, { children: [
    jsx(Head, { eyebrow: 'My Bots', title: some ? 'What you added with Cabinet' : 'My Bots',
      lead: some ? 'Open a chat with any of them, or remove one you haven’t changed' : null }, 'head'),
    error ? jsx(Problem, { error, retry: load }, 'error') : null,
    (mine.overlaps || []).length ? jsx(Notice, { tone: 'warn', icon: 'warning', title: 'Also in Hermes Bots',
      action: jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => openHermesBots(null), children: 'Open Hermes Bots' }),
      children: jsxs(Fragment, { children: [
        jsx('p', { children: 'Your Hermes now has its own Bots catalog, which also carries these:' }),
        jsx('ul', { className: 'bc-ul', children: mine.overlaps.map(o => jsx('li', { children: o.message }, o.profile)) }),
      ] }) }, 'overlaps') : null,
    !data && !error ? jsx(SkeletonPage, { header: false, label: 'Loading your bots', cards: [{ title: true, lines: 3 }] }, 'loading') : null,
    nothing ? jsx(Empty, { icon: 'briefcase', title: 'Nothing added yet',
      body: 'Bots and crews you add with Cabinet show up here, ready to chat with or remove',
      action: jsxs('div', { className: 'hk-actions', style: { justifyContent: 'center' }, children: [
        jsx(Button, { onClick: () => go({ name: 'cabinet' }), children: 'Browse The Cabinet' }),
        jsx(Button, { variant: 'secondary', onClick: () => go({ name: 'crews' }), children: 'See the Crew Kits' }),
      ] }) }, 'empty') : null,
    mine.bots.length ? jsxs('section', { className: 'hk-section', children: [
      jsxs('div', { className: 'bc-sec-head', children: [jsx('h2', { className: 'hk-h2 bc-serif', children: 'Bots' }), jsx('span', { className: 'hk-count', children: String(mine.bots.length) })] }),
      jsx('div', { className: 'bc-minegrid hk-enter-list', children: mine.bots.map(b => jsxs(Card, { className: 'bc-mine', children: [
        jsxs('div', { className: 'bc-mine-head', children: [
          jsx(Face, { bot: faceFor(b.source && b.source.slug) || { name: b.displayName }, label: b.displayName, size: 44 }),
          jsxs('div', { className: 'hk-stack', style: { gap: 2 }, children: [jsx('strong', { children: b.displayName }),
            jsx('span', { className: 'hk-small', children: b.source ? `From ${b.source.name} · added ${shortDate(localDay(b.createdAt))}` : `Bot Lab plan · added ${shortDate(localDay(b.createdAt))}` })] }),
          jsx('span', { className: 'hk-grow' }),
          jsx(Pill, { tone: STATUS_TONE[b.status] || 'idle', dot: true, children: b.statusText }),
        ] }),
        jsx('p', { className: 'hk-mono bc-mine-where', children: b.where }),
        b.reasons.length === 1 ? jsxs('p', { className: 'hk-small', children: ['Why Cabinet won’t remove it: ', lowerFirst(b.reasons[0])] })
          : b.reasons.length ? jsxs('div', { className: 'hk-small', children: ['Why Cabinet won’t remove it:', jsx(List, { items: b.reasons })] }) : null,
        jsxs('div', { className: 'hk-actions', children: [
          b.status !== 'missing' ? jsxs(Button, { size: 'sm', onClick: () => openBotChat(b.profile), children: [Icon('comment', '', 12), 'Open chat'] }) : null,
          b.canRemove || b.status === 'changed' ? jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => setOpen({ kind: 'remove', rk: 'bot', id: b.id, name: b.displayName }), children: b.canRemove ? 'Remove' : 'Why it stays' }) : null,
          b.canForget ? jsx(Button, { variant: 'secondary', size: 'sm', onClick: async () => { try { await post(`forget/${b.id}`, {}); after() } catch (e) { setError(describeError(e)) } }, children: 'Forget it' }) : null,
        ] }),
        panelFor('bot', b.id),
      ] }, b.id)) }),
    ] }, 'bots') : null,
    mine.crews.length ? jsxs('section', { className: 'hk-section', children: [
      jsxs('div', { className: 'bc-sec-head', children: [jsx('h2', { className: 'hk-h2 bc-serif', children: 'Crews' }), jsx('span', { className: 'hk-count', children: String(mine.crews.length) })] }),
      jsx('div', { className: 'hk-stack', style: { gap: 12 }, children: mine.crews.map(c => jsxs(Card, { className: 'bc-mine bc-minecrew', children: [
        jsxs('div', { className: 'bc-mine-head', children: [
          jsxs('div', { className: 'hk-stack', style: { gap: 2 }, children: [jsx('strong', { children: c.name }), jsx('span', { className: 'hk-small', children: `${c.members.length} bots · ${c.steps.length} steps · you step in ${timesWord(c.steps.filter(s => s.you).length)}` })] }),
          jsx('span', { className: 'hk-grow' }),
          jsx(Pill, { tone: c.status === 'active' ? 'good' : 'warn', dot: true, children: c.statusText }),
        ] }),
        jsx('ul', { className: 'bc-crewchips', children: c.members.map(m => jsxs('li', { children: [jsx(Face, { bot: BOT[m.slug], size: 28 }), jsxs('span', { children: [jsx('strong', { children: m.name }),
          jsx('span', { className: 'hk-small', children: m.mode === 'existing' ? `yours: ${m.profile}` : m.mode === 'missing' ? 'not made' : m.profile })] }),
          m.install && m.install.status === 'changed' ? jsx(Pill, { tone: 'warn', children: 'Changed' }) : null,
          m.mode !== 'missing' ? jsx('button', { type: 'button', className: 'hk-link', onClick: () => openBotChat(m.profile), children: 'Open chat' }) : null] }, m.slug)) }),
        jsx('p', { className: 'hk-small', children: 'Hermes doesn’t follow a crew’s order by itself. Move work along with Kanban cards, or start a group chat with these bots to talk it through' }),
        jsxs('div', { className: 'hk-actions', children: [
          c.status === 'incomplete' ? jsx(Button, { size: 'sm', onClick: () => go({ name: 'setup', id: c.slug }), children: 'Finish setting it up' }) : null,
          c.status === 'active' ? jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => setOpen({ kind: 'cards', id: c.id }), children: c.cardsMade ? 'See its Kanban cards' : 'Make Kanban cards' }) : null,
          jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => go({ name: 'crew', id: c.slug }), children: 'See the crew' }),
          jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => setOpen({ kind: 'remove', rk: 'crew', id: c.id, name: c.name }), children: 'Remove crew' }),
        ] }),
        panelFor('crew', c.id),
      ] }, c.id)) }),
    ] }, 'crews') : null,
    viaHermes.length ? jsxs('section', { className: 'hk-section', children: [
      jsxs('div', { className: 'bc-sec-head', children: [jsx('h2', { className: 'hk-h2 bc-serif', children: 'Added through Hermes Bots' }), jsx('span', { className: 'hk-count', children: String(viaHermes.length) })] }),
      jsx('p', { className: 'hk-small', children: 'Hermes’s own Bots catalog added these, and Hermes looks after them. Open a chat with any of them from your Bots list' }),
      jsx('div', { className: 'bc-minegrid', children: viaHermes.map(h => jsxs(Card, { className: 'bc-mine', children: [
        jsxs('div', { className: 'bc-mine-head', children: [
          jsx(Face, { bot: faceFor(h.slug) || { name: h.name }, label: h.name, size: 44 }),
          jsxs('div', { className: 'hk-stack', style: { gap: 2 }, children: [jsx('strong', { children: h.name }), jsx('span', { className: 'hk-small hk-mono', children: h.profile })] }),
          jsx('span', { className: 'hk-grow' }),
          jsx(Pill, { tone: 'good', dot: true, children: 'Already added through Hermes Bots' }),
        ] }),
        jsx('div', { className: 'hk-actions', children: jsxs(Button, { size: 'sm', onClick: () => openBotChat(h.profile), children: [Icon('comment', '', 12), 'Open chat'] }) }),
      ] }, h.profile)) }),
    ] }, 'hermesbots') : null,
    mine.kits.length ? jsxs('section', { className: 'hk-section', children: [
      jsxs('div', { className: 'bc-sec-head', children: [jsx('h2', { className: 'hk-h2 bc-serif', children: 'Saved kits' }), jsx('span', { className: 'hk-count', children: String(mine.kits.length) })] }),
      jsx('p', { className: 'hk-small', children: 'A kit is a Bot Lab plan you saved. It is not a bot until you make one from it' }),
      jsx('div', { className: 'bc-minegrid', children: mine.kits.map(k => jsxs(Card, { className: 'bc-mine', children: [
        jsxs('div', { className: 'bc-mine-head', children: [Icon('beaker', '', 18), jsxs('div', { className: 'hk-stack', style: { gap: 2 }, children: [jsx('strong', { children: k.name }),
          jsx('span', { className: 'hk-small', children: k.job || 'A saved plan' })] })] }),
        jsxs('div', { className: 'hk-actions', children: [
          jsx(Button, { size: 'sm', onClick: () => setOpen({ kind: 'kit', id: k.id, kit: k }), children: 'Make a bot from it' }),
          jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => setOpen({ kind: 'remove', rk: 'kit', id: k.id, name: k.name }), children: 'Remove kit' }),
        ] }),
        panelFor('kit', k.id),
      ] }, k.id)) }),
    ] }, 'kits') : null,
    data && mine.history.length ? jsx(Details, { label: 'What Cabinet has done, newest last', children: jsx('ul', { className: 'bc-ul bc-plain', children: mine.history.map((h, i) => jsxs('li', { children: [
      jsx('span', { className: 'hk-mono hk-small', children: (h.at || '').replace('T', ' ').replace('Z', '') }), ` · ${h.action} · ${h.subject}${h.detail ? `, ${h.detail}` : ''}`] }, i)) }) }, 'history') : null,
    some ? jsxs('p', { className: 'hk-small bc-source', style: { flexWrap: 'nowrap', alignItems: 'flex-start' }, children: [Icon('folder', '', 12), `Profiles live in ${mine.where}`] }, 'where') : null,
  ] })
}

/* ------------------------------------------------------------------ page -- */

function Page() {
  const view = useValue($view)
  useValue($catalog) // re-render everything when the catalog changes
  useEffect(() => { loadCatalog(false) }, [])
  if (view.name === 'add') return jsx(AddBot, { id: view.id }, view.id)
  if (view.name === 'setup') return jsx(SetUpCrew, { id: view.id }, view.id)
  if (view.name === 'mybots') return jsx(MyBots, {})
  if (view.name === 'answer' && view.data) return jsx(Answer, { out: view.data })
  if (view.name === 'fit' || view.name === 'answer') return jsx(FitForm, {})
  if (view.name === 'cabinet') return jsx(Cabinet, {})
  if (view.name === 'bot') return jsx(BotPage, { id: view.id }, view.id)
  if (view.name === 'crews') return jsx(Crews, {})
  if (view.name === 'crew') return jsx(CrewPage, { id: view.id, from: view.from }, view.id)
  if (view.name === 'records') return jsx(Records, {})
  if (view.name === 'lab') return jsx(BotLab, {})
  return jsx(StartHere, {})
}

// Cabinet's brass is the app's own yellow mixed toward the text color, so it reads on light and dark
// skins alike. The wordmark is the site's PNG used as a mask and filled with theme colors.
const CSS = `
.bc{--bc-brass-fill:var(--ui-yellow,var(--hk-warn-fill));--bc-brass:color-mix(in srgb,var(--bc-brass-fill) 74%,var(--hk-ink));
  --bc-brass-wash:color-mix(in srgb,var(--bc-brass-fill) 11%,transparent);--bc-brass-line:color-mix(in srgb,var(--bc-brass-fill) 46%,transparent);
  --bc-serif:"DM Serif Display","DM Serif Text",Georgia,"Times New Roman",serif;
  --bc-shadow:0 1px 2px color-mix(in srgb,var(--hk-ink) 8%,transparent),0 8px 24px -12px color-mix(in srgb,var(--hk-ink) 30%,transparent)}
.bc .hk-page-inner{max-width:1120px;gap:18px}
.bc .hk-page[data-width=narrow] .hk-page-inner{max-width:820px}
.bc-display,.bc-serif,.bc-tile-name,.bc-crewrow-name,.bc-proof-title{font-family:var(--bc-serif);font-weight:400;letter-spacing:0;font-variant-numeric:lining-nums}
.hk-h1.bc-display{font-size:24px;line-height:1.18}
.hk-h2.bc-serif{font-size:16px}
.bc-eyebrow{color:var(--bc-brass);text-transform:uppercase;letter-spacing:.08em;font-size:10.5px}
.bc-head{gap:6px}
.bc-subtitle{font-size:13px;font-weight:600}
.bc-ext{font-size:11px}
.bc-linkwrap{display:inline-flex;flex-wrap:wrap;gap:6px;align-items:center}
.bc-address{display:inline-flex;gap:8px;align-items:center;padding:2px 8px;border-radius:var(--hk-radius-sm);background:var(--hk-sunken);word-break:break-all}
.bc-sitebtn{display:inline-flex;flex-direction:column;gap:4px;align-items:flex-start}
.bc-sitebtn button{gap:6px}
.bc-addr-hint{font-size:11px}
.bc-credit{display:flex;align-items:center;gap:6px;font-size:12px}
.bc-credit .codicon{color:var(--bc-brass)}
.bc-credits{display:grid;gap:2px;margin-top:2px}
.bc-alongside-list{list-style:none;margin:6px 0 0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,230px),1fr));gap:10px}
.bc-alongside-item{all:unset;box-sizing:border-box;cursor:pointer;display:flex;gap:10px;align-items:flex-start;width:100%;height:100%;padding:12px;border:1px solid var(--hk-stroke);border-radius:var(--hk-radius);background:var(--hk-surface)}
.bc-alongside-item>.codicon{color:var(--bc-brass);margin-top:2px}
.bc-alongside-item>span{display:grid;gap:3px;font-size:12.5px;line-height:1.45}
.bc-alongside-item:hover{border-color:var(--bc-brass-line);background:var(--bc-brass-wash)}
.bc-alongside-item:focus-visible{outline:2px solid var(--hk-accent);outline-offset:1px}
.bc-source{display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding-top:6px;border-top:1px solid var(--hk-stroke)}
.bc-source-note{color:var(--hk-ink);font-weight:400}
.bc-source[data-tone=warn] .codicon{color:var(--hk-warn)}

/* frame */
.bc-top{position:sticky;top:0;z-index:2;background:color-mix(in srgb,var(--hk-bg) 92%,transparent);backdrop-filter:blur(10px);border-bottom:1px solid var(--hk-stroke)}
.bc-top-row{display:flex;align-items:center;gap:14px;padding:10px 24px 6px;max-width:1168px;margin:0 auto}
.bc-brand{all:unset;cursor:pointer;display:flex;align-items:center;border-radius:var(--hk-radius-sm);padding:2px}
.bc-brand:focus-visible{outline:2px solid var(--hk-accent);outline-offset:2px}
.bc-wordmark{display:block;width:124px;height:26px;background:linear-gradient(90deg,var(--bc-brass) 0 37%,var(--hk-ink) 37%);
  -webkit-mask:url(${IMAGES.wordmark}) center/contain no-repeat;mask:url(${IMAGES.wordmark}) center/contain no-repeat}
.bc-tagline{display:flex;align-items:center;gap:7px;font-size:11.5px;font-weight:500;white-space:nowrap}
.bc-lamp{width:7px;height:7px;border-radius:50%;background:var(--hk-good-fill);box-shadow:0 0 0 3px color-mix(in srgb,var(--hk-good-fill) 22%,transparent)}
.bc-here{all:unset;cursor:pointer;display:inline-flex;gap:6px;align-items:center;padding:3px 8px;border-radius:999px;border:1px solid var(--hk-stroke);font-size:11px;color:var(--hk-ink)}
.bc-here:hover{border-color:var(--bc-brass-line);background:var(--bc-brass-wash)}
.bc-here:focus-visible{outline:2px solid var(--hk-accent);outline-offset:1px}
.bc-tabs{display:flex;gap:2px;padding:0 18px;max-width:1168px;margin:0 auto;overflow-x:auto;scrollbar-width:none}
.bc-tab{all:unset;cursor:pointer;display:inline-flex;align-items:center;gap:6px;padding:7px 10px 9px;font-size:12.5px;font-weight:500;color:var(--hk-ink);white-space:nowrap;border-bottom:2px solid transparent;
  transition:border-color var(--hk-dur) var(--hk-ease),background-color var(--hk-dur) var(--hk-ease)}
.bc-tab:hover{background:var(--hk-hover)}
.bc-tab[aria-selected=true]{border-bottom-color:var(--bc-brass-fill);font-weight:650}
.bc-tab[aria-selected=true] .codicon{color:var(--bc-brass)}
.bc-tab:focus-visible{outline:2px solid var(--hk-accent);outline-offset:-2px}

/* hero and scenes */
.bc-hero,.bc-scene{position:relative;overflow:hidden;border-radius:calc(var(--hk-radius) + 4px);border:1px solid var(--hk-stroke);min-height:300px;display:flex;align-items:center;box-shadow:var(--bc-shadow);isolation:isolate}
.bc-scene{min-height:210px}
.bc-hero-art,.bc-scene-art{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:center 40%;z-index:-2}
.bc-scene-art{object-position:center 55%}
.bc-hero-scrim{position:absolute;inset:0;z-index:-1;background:linear-gradient(90deg,var(--hk-bg) 0%,color-mix(in srgb,var(--hk-bg) 88%,transparent) 34%,color-mix(in srgb,var(--hk-bg) 30%,transparent) 64%,transparent 100%)}
.bc-hero-copy,.bc-scene-copy{display:grid;gap:10px;padding:28px 30px;max-width:520px}
.bc-scene-copy{max-width:470px;padding:22px 26px}
.bc-kicker{font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--bc-brass)}
.bc-hero-title{font-size:22px !important;line-height:1.15}
.bc-hero .bc-hero-title{font-size:28px !important;line-height:1.12}
.bc-hero-deck{font-size:13px;line-height:1.55;max-width:46ch}
.bc-hero-facts{display:flex;gap:6px;flex-wrap:wrap;font-size:11.5px;font-weight:600}
.bc-hero-facts span{padding:2px 8px;border-radius:999px;background:color-mix(in srgb,var(--hk-surface) 80%,transparent);border:1px solid var(--hk-stroke)}

/* panels */
.bc-panel{gap:10px;padding:16px 18px}
.bc-panel-head{display:flex;gap:16px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap}
.bc-panel-head>.hk-stack{flex:1;min-width:240px}
.bc-two{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:14px}
.bc-sec-head{display:flex;align-items:center;gap:8px}

/* ladder */
.bc-ladder{position:relative;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;padding-top:4px}
.bc-ladder-line{position:absolute;left:0;right:0;top:4px;height:100px;width:100%;color:var(--bc-brass);pointer-events:none;z-index:1}
.bc-rung{all:unset;box-sizing:border-box;cursor:pointer;display:grid;grid-template-rows:100px auto auto;gap:3px;justify-items:center;text-align:center;border-radius:var(--hk-radius);padding-bottom:6px}
.bc-rung-bar{align-self:end;width:100%;max-width:120px;position:relative;border-radius:4px 4px 0 0;border-top:2px solid var(--bc-brass-fill);
  background:linear-gradient(180deg,color-mix(in srgb,var(--bc-brass-fill) 22%,transparent),color-mix(in srgb,var(--bc-brass-fill) 5%,transparent));transition:background-color var(--hk-dur) var(--hk-ease)}
.bc-rung-bar::before{content:"";position:absolute;left:50%;top:-6px;width:10px;height:10px;margin-left:-5px;border-radius:50%;background:var(--hk-surface);border:2px solid var(--bc-brass-fill);z-index:2}
.bc-rung-num{position:absolute;left:8px;bottom:5px;font-size:11px;font-weight:700}
.bc-rung-label{display:inline-flex;gap:5px;align-items:center;font-weight:650;font-size:12.5px;margin-top:4px}
.bc-rung-label .codicon{color:var(--bc-brass)}
.bc-rung-note{font-size:11.5px}
.bc-rung:hover .bc-rung-bar{background:color-mix(in srgb,var(--bc-brass-fill) 26%,transparent)}
.bc-rung:focus-visible{outline:2px solid var(--hk-accent);outline-offset:2px}
.bc-banner{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,420px);gap:20px;align-items:end;padding:16px 18px;border:1px solid var(--hk-stroke);border-radius:calc(var(--hk-radius) + 4px);
  background:linear-gradient(135deg,var(--bc-brass-wash),transparent 60%),var(--hk-surface)}
.bc-banner-ladder .bc-rung,div.bc-rung{cursor:default}
.bc-banner-ladder .bc-ladder{transform-origin:bottom}
@container (max-width:820px){.bc-banner{grid-template-columns:1fr}}

/* tiles */
.bc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));gap:14px}
.bc-strip{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px}
@container (max-width:900px){.bc-strip{grid-template-columns:repeat(3,minmax(0,1fr))}}
.bc-tile{all:unset;box-sizing:border-box;cursor:pointer;display:grid;grid-template-rows:auto 1fr;background:var(--hk-surface);border:1px solid var(--hk-stroke);border-radius:calc(var(--hk-radius) + 2px);overflow:hidden;
  transition:border-color var(--hk-dur) var(--hk-ease),transform var(--hk-dur) var(--hk-ease),box-shadow var(--hk-dur) var(--hk-ease)}
.bc-tile:hover{border-color:var(--bc-brass-line);transform:translateY(-2px);box-shadow:var(--bc-shadow)}
.bc-tile:focus-visible{outline:2px solid var(--hk-accent);outline-offset:2px}
.bc-tile-art{position:relative;display:block;aspect-ratio:4/5;background:var(--hk-sunken)}
.bc-tile-compact .bc-tile-art{aspect-ratio:1/1}
.bc-tile-art .bc-portrait{position:absolute;inset:0}
.bc-tile-body{display:grid;gap:2px;padding:10px 12px 12px;align-content:start}
.bc-tile-compact .bc-tile-body{padding:8px 10px 10px}
.bc-tile-name{font-family:var(--bc-serif);font-size:16px;line-height:1.2}
.bc-tile-compact .bc-tile-name{font-size:14px}
.bc-tile-title{font-size:11.5px;font-weight:600;color:var(--bc-brass)}
.bc-tile-sum{font-size:12px;line-height:1.45;margin-top:3px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.bc-tile-credit{display:flex;gap:5px;align-items:flex-start;font-size:11px;font-style:italic;margin-top:5px}
.bc-tile-credit>span{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.bc-tile-credit .codicon{color:var(--bc-brass);margin-top:2px}

/* portraits */
.bc-portrait{display:block;width:100%;height:100%;overflow:hidden;background-color:var(--hk-sunken);
  background-image:linear-gradient(90deg,transparent 0,color-mix(in srgb,var(--hk-ink) 7%,transparent) 50%,transparent 100%);background-size:200% 100%;background-repeat:no-repeat;animation:hk-shimmer 1.4s linear infinite}
.bc-portrait.bc-ready{animation:none;background-image:none}
.bc-portrait img{display:block;width:100%;height:100%;object-fit:cover;object-position:center 22%;opacity:0;transition:opacity 200ms var(--hk-ease)}
.bc-portrait.bc-ready img{opacity:1}
.bc-portrait-initial{display:grid;place-items:center;width:100%;height:100%;font-family:var(--bc-serif);font-size:20px}
.bc-face{position:relative;flex:none;width:var(--bc-size);height:var(--bc-size);border-radius:50%;overflow:hidden;display:inline-grid;place-items:center;background:var(--hk-sunken);border:1.5px solid var(--hk-surface);box-shadow:0 0 0 1px var(--hk-stroke)}
.bc-face .bc-portrait img{object-position:center 18%;transform:scale(1.55);transform-origin:50% 26%}
/* The painted portraits are dark scenes; cropped to a face they need lifting to read at 28 to 56px. */
.bc-face .bc-portrait img{filter:brightness(1.32) contrast(1.06) saturate(1.05)}
.bc-face-ring{box-shadow:0 0 0 1.5px var(--bc-brass-line)}
.bc-face-you{background:var(--bc-brass-wash);color:var(--bc-brass);box-shadow:0 0 0 1.5px var(--bc-brass-fill)}
.bc-faces{display:inline-flex}
.bc-faces .bc-face+.bc-face{margin-left:-7px}

/* badges */
.bc-badge{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:650;line-height:16px;padding:2px 8px;border-radius:999px;white-space:nowrap;color:var(--hk-ink);
  border:1px solid var(--hk-stroke-strong);background:var(--hk-surface)}
.bc-badge[data-tested]{border-color:color-mix(in srgb,var(--hk-good-fill) 55%,transparent);background:color-mix(in srgb,var(--hk-good-fill) 14%,var(--hk-surface))}
.bc-badge[data-tested] .codicon{color:var(--hk-good)}
.bc-badge-over{position:absolute;left:8px;bottom:8px;z-index:1;background:color-mix(in srgb,var(--hk-surface) 90%,transparent);backdrop-filter:blur(6px);box-shadow:0 1px 3px color-mix(in srgb,var(--hk-ink) 18%,transparent)}
.bc-badge-over[data-tested]{background:color-mix(in srgb,var(--hk-good-fill) 18%,color-mix(in srgb,var(--hk-surface) 92%,transparent))}

/* cabinet */
.bc-toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.bc-search{display:flex;align-items:center;gap:8px;flex:1;min-width:240px;max-width:420px;padding:0 10px;border:1px solid var(--hk-stroke);border-radius:var(--hk-radius);background:var(--hk-surface)}
.bc-search:focus-within{border-color:var(--hk-accent)}
.bc-search .bc-search-input,.bc-search input{border:0 !important;background:transparent !important;box-shadow:none !important;outline:none;flex:1;min-width:0;padding:6px 0;font-size:13px}
.bc-chips,.bc-crewlinks{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.bc-chip{all:unset;cursor:pointer;display:inline-flex;gap:5px;align-items:center;font-size:12px;font-weight:500;padding:3px 10px;border-radius:999px;border:1px solid var(--hk-stroke);color:var(--hk-ink);background:var(--hk-surface);
  transition:border-color var(--hk-dur) var(--hk-ease),background-color var(--hk-dur) var(--hk-ease)}
.bc-chip:hover{border-color:var(--bc-brass-line)}
.bc-chip[aria-pressed=true]{border-color:var(--bc-brass-fill);background:var(--bc-brass-wash);font-weight:650}
.bc-chip:focus-visible{outline:2px solid var(--hk-accent);outline-offset:1px}
.bc-count{margin-top:-6px}
.bc-bad{color:var(--hk-bad)}

/* bot page */
.bc-step{all:unset;cursor:pointer;display:inline-flex;gap:4px;align-items:center;font-size:12px;padding:3px 8px;border-radius:var(--hk-radius-sm)}
.bc-step:hover{background:var(--hk-hover)}
.bc-step:focus-visible{outline:2px solid var(--hk-accent)}
.bc-detail{display:grid;grid-template-columns:300px minmax(0,1fr);gap:22px;align-items:start}
.bc-detail-side{position:sticky;top:96px;display:grid;gap:10px}
.bc-detail-art{position:relative;aspect-ratio:4/5;border-radius:calc(var(--hk-radius) + 4px);overflow:hidden;border:1px solid var(--hk-stroke);box-shadow:var(--bc-shadow)}
.bc-detail-art .bc-portrait{position:absolute;inset:0}
.bc-detail-art .bc-badge-over{left:10px;bottom:10px}
.bc-caption{font-style:italic}
.bc-detail-side>button{width:100%}
.bc-detail-side .bc-sitebtn,.bc-detail-side .bc-sitebtn>button{width:100%}
@container (max-width:860px){.bc-detail{grid-template-columns:1fr}.bc-detail-side{position:static;grid-template-columns:200px minmax(0,1fr);align-items:start}.bc-detail-art{grid-row:span 4}}
.bc-record{gap:12px}
.bc-record-cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@container (max-width:700px){.bc-record-cols{grid-template-columns:1fr}}
.bc-record-col{display:grid;gap:6px;align-content:start}
.bc-checks{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.bc-checks li{display:grid;grid-template-columns:18px minmax(0,1fr);gap:6px;font-size:12.5px}
.bc-checks li>div{display:grid;gap:2px}
.bc-check-icon{margin-top:2px}
.bc-checks li[data-done] .bc-check-icon{color:var(--hk-good)}
.bc-date{font-weight:500}
.bc-note{padding-top:8px;border-top:1px solid var(--hk-stroke)}
.bc-partners{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px}
.bc-partner,.bc-member,.bc-wait,.bc-labpick{all:unset;box-sizing:border-box;cursor:pointer;display:flex;gap:10px;align-items:center;padding:6px 8px;border-radius:var(--hk-radius);border:1px solid transparent;
  transition:border-color var(--hk-dur) var(--hk-ease),background-color var(--hk-dur) var(--hk-ease)}
.bc-partner:hover,.bc-member:hover,.bc-wait:hover,.bc-labpick:hover{background:var(--hk-hover);border-color:var(--hk-stroke)}
.bc-partner:focus-visible,.bc-member:focus-visible,.bc-wait:focus-visible,.bc-labpick:focus-visible{outline:2px solid var(--hk-accent)}
.bc-partner>span:last-child,.bc-labpick>span:last-child{display:grid;gap:0}
.bc-crewlinks{padding-top:4px}

/* crews */
.bc-crewlist{display:grid;gap:10px}
.bc-crewrow{all:unset;box-sizing:border-box;cursor:pointer;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,400px);gap:20px;align-items:center;padding:14px 18px;background:var(--hk-surface);border:1px solid var(--hk-stroke);border-radius:calc(var(--hk-radius) + 2px);
  transition:border-color var(--hk-dur) var(--hk-ease),transform var(--hk-dur) var(--hk-ease),box-shadow var(--hk-dur) var(--hk-ease)}
.bc-crewrow:hover{border-color:var(--bc-brass-line);transform:translateY(-1px);box-shadow:var(--bc-shadow)}
.bc-crewrow:focus-visible{outline:2px solid var(--hk-accent);outline-offset:2px}
@container (max-width:820px){.bc-crewrow{grid-template-columns:1fr}}
.bc-crewrow-copy{display:grid;gap:4px}
.bc-crewrow-name{font-family:var(--bc-serif);font-size:16px}
.bc-crewrow-sum{font-size:12.5px}
.bc-track{position:relative;display:grid;--bc-node:52px}
.bc-track-rail{position:absolute;left:0;right:0;top:0;height:var(--bc-node);color:var(--bc-brass);pointer-events:none}
.bc-track-line{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.bc-track-arrow{position:absolute;top:50%;transform:translate(-50%,-50%);color:var(--bc-brass);display:flex}
.bc-track-steps{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(var(--bc-n),minmax(0,1fr));gap:0;position:relative}
.bc-track-step{display:grid;justify-items:center;align-content:start;gap:8px;text-align:center;min-width:0;padding:0 6px}
.bc-track-node{position:relative;display:flex;align-items:center;justify-content:center;height:var(--bc-node)}
.bc-track-node .bc-face+.bc-face{margin-left:-10px;align-self:flex-end}
.bc-track-n{position:absolute;top:-4px;left:50%;margin-left:calc(var(--bc-node) / 2 - 8px);min-width:17px;height:17px;padding:0 4px;border-radius:999px;font-size:10px;font-weight:700;line-height:17px;text-align:center;
  background:var(--hk-surface);border:1px solid var(--bc-brass-line);color:var(--hk-ink)}
.bc-track[data-size=sm] .bc-track-n{min-width:14px;height:14px;line-height:14px;font-size:9px;top:-5px}
.bc-track-cap{display:grid;gap:3px;justify-items:center;font-size:12px}
.bc-track-cap strong{font-size:12.5px}
.bc-track-owner{font-size:11.5px;font-weight:600;color:var(--bc-brass)}
.bc-track-act{font-size:12px;line-height:1.45}
.bc-stepin{display:inline-flex;gap:4px;align-items:center;font-size:11px;font-weight:650;padding:1px 8px;border-radius:999px;background:var(--bc-brass-wash);border:1px solid var(--bc-brass-line)}
.bc-track-mini{font-size:11px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.bc-members{list-style:none;margin:0;padding:0;display:grid;gap:4px}
.bc-member{width:100%}
.bc-member>.hk-grow{display:grid;gap:2px}
.bc-member-job{font-size:12.5px}

/* records */
.bc-tally{display:grid;gap:8px}
.bc-tally-bar{width:100%;height:10px;border-radius:5px;overflow:hidden;background:var(--hk-sunken)}
.bc-tally-key{list-style:none;margin:0;padding:0;display:grid;gap:3px;font-size:12px}
.bc-tally-key li{display:flex;gap:6px;align-items:center}
.bc-key{width:10px;height:10px;border-radius:3px;flex:none}
rect.bc-tone-good{fill:var(--hk-good-fill)}rect.bc-tone-info{fill:color-mix(in srgb,var(--hk-good-fill) 55%,var(--hk-surface))}rect.bc-tone-bad{fill:var(--hk-bad-fill)}rect.bc-tone-idle{fill:var(--hk-idle-fill)}
span.bc-tone-good{background:var(--hk-good-fill)}span.bc-tone-info{background:color-mix(in srgb,var(--hk-good-fill) 55%,var(--hk-surface))}span.bc-tone-bad{background:var(--hk-bad-fill)}span.bc-tone-idle{background:var(--hk-idle-fill)}
.bc-notice{display:flex;gap:10px;align-items:flex-start;padding:12px 14px;border-radius:var(--hk-radius);border:1px solid var(--bc-brass-line);background:var(--bc-brass-wash)}
.bc-notice>.codicon{color:var(--bc-brass);margin-top:1px}
.bc-proofs{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,460px),1fr));gap:12px}
.bc-proof{display:grid;grid-template-columns:120px minmax(0,1fr);background:var(--hk-surface);border:1px solid var(--hk-stroke);border-radius:calc(var(--hk-radius) + 2px);overflow:hidden}
.bc-proof-art{position:relative;background:var(--hk-sunken);min-height:150px;display:grid;place-items:center}
.bc-proof-art>.bc-portrait{position:absolute;inset:0}
.bc-proof-faces{display:grid;grid-template-columns:repeat(2,38px);gap:6px;justify-content:center}
.bc-proof-body{display:grid;gap:5px;padding:12px 14px;align-content:start}
.bc-proof-title{font-family:var(--bc-serif);font-weight:400;font-size:15px}
.bc-proof-out{font-weight:600;font-size:12.5px}
.bc-waiting{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:2px 6px}
.bc-wait{font-size:12.5px;padding:4px 6px}
.bc-kinds4{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:10px}
.bc-kindcard{gap:4px}
.bc-kindcard p{font-size:12.5px}

/* fit test */
.bc-side{position:sticky;top:100px}
.bc-form{gap:0;padding:4px 14px 12px}
.bc-form>.hk-field{padding:10px 0}
.bc-job{font-size:14px}
.bc-q{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 0;border-top:1px solid var(--hk-stroke);flex-wrap:wrap}
.bc-q-text{font-weight:500;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.bc-grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.bc-details{border-top:1px solid var(--hk-stroke);padding:9px 0}
.bc-submit{display:grid;gap:10px;padding-top:12px;border-top:1px solid var(--hk-stroke)}
.bc-error{color:var(--hk-ink);border-left:3px solid var(--hk-bad-fill);padding:5px 9px;background:color-mix(in srgb,var(--hk-bad-fill) 8%,transparent);border-radius:4px;font-size:12px}
.bc-okline{display:flex;gap:6px;align-items:center;font-weight:600}
.bc-okline .codicon{color:var(--hk-good)}
.bc-jobs{display:grid;gap:6px}
.bc-job-card{display:flex;gap:10px;align-items:center;text-align:left;font:inherit;color:var(--hk-ink);background:var(--hk-surface);border:1px solid var(--hk-stroke);border-radius:var(--hk-radius);padding:7px 10px;cursor:pointer;
  transition:border-color var(--hk-dur) var(--hk-ease),background-color var(--hk-dur) var(--hk-ease)}
.bc-job-card:hover{border-color:var(--bc-brass-line)}
.bc-job-card:focus-visible{outline:2px solid var(--hk-accent);outline-offset:1px}
.bc-job-copy{display:grid;gap:1px;min-width:0}
.bc-job-title{font-weight:600;font-size:13px}
.bc-job-meta{font-size:12px}
.bc-job-credit{font-size:12px;font-style:italic}
.bc-kinds{list-style:none;margin:0;padding:0;display:grid;border:1px solid var(--hk-stroke);border-radius:var(--hk-radius);overflow:hidden;background:var(--hk-surface)}
.bc-kind{display:flex;gap:8px;align-items:flex-start;padding:8px 10px;border-top:1px solid var(--hk-stroke)}
.bc-kind:first-child{border-top:0}
.bc-kind .codicon{margin-top:2px;color:var(--bc-brass)}
.bc-kind-title{font-weight:600;font-size:13px}
.bc-kind-meaning{font-size:12px}
.bc-kind-on{background:var(--hk-active);box-shadow:inset 2px 0 0 var(--hk-accent)}
.bc-flows{display:flex}
.bc-botline{display:flex;gap:12px;align-items:center;padding:10px 12px;border-radius:var(--hk-radius);background:var(--hk-sunken)}
.bc-botline-art{flex:none;width:56px;height:70px;border-radius:var(--hk-radius-sm);overflow:hidden;border:1px solid var(--hk-stroke)}
.bc-steps{margin:0;padding-left:18px;display:grid;gap:3px}
.bc-ul{margin:0;padding-left:16px;display:grid;gap:2px}
.bc-ul.bc-plain{list-style:none;padding:0}
.bc-path{list-style:none;margin:0;padding:0;display:grid}
.bc-path-row{display:grid;grid-template-columns:20px minmax(0,1fr);gap:8px}
.bc-path-rail{display:flex;flex-direction:column;align-items:center;padding-top:4px}
.bc-path-node{color:var(--hk-good-fill);flex:none}
.bc-path-link{flex:1;width:2px;min-height:10px;color:color-mix(in srgb,var(--hk-good-fill) 55%,var(--hk-stroke));margin:2px 0 -4px}
.bc-path-body{display:grid;gap:1px;padding-bottom:10px;align-content:start}
.bc-path-goal{width:20px;height:20px;border-radius:50%;display:grid;place-items:center;background:var(--hk-accent);color:var(--dt-primary-foreground,var(--hk-bg))}
.bc-path-goal .codicon{font-size:11px !important}
.bc-path-end .bc-path-body{padding:1px 0 0}
.bc-why-q{font-size:12px;font-weight:600}
.bc-why-effect{font-size:12px}
.bc-unused{margin:0;padding-left:16px;display:grid;gap:2px;font-size:12px}

/* bot lab */
.bc-labhint{display:grid;gap:8px}
.bc-labpicks{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:4px}
.bc-plan{list-style:none;margin:0;padding:0;display:grid;gap:0}
.bc-plan li{display:grid;gap:5px;padding:10px 0;border-top:1px solid var(--hk-stroke);transition:box-shadow 140ms ease,padding 140ms ease}
.bc-plan li.bc-kept{box-shadow:inset 2px 0 0 var(--hk-good-fill);padding-left:10px}
.bc-plan-head{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.bc-plan-label{font-weight:600;font-size:12px}
.bc-soul{white-space:pre-wrap;font-family:var(--hk-mono);font-size:12px;line-height:1.5;margin:0;padding:10px;border-radius:6px;background:var(--hk-sunken);max-height:260px;overflow:auto}
.bc-plain-links{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px}
/* add, set up a crew, my bots */
.bc-tilebox{position:relative;display:grid}
.bc-tilebox>.bc-tile{width:100%}
.bc-add-tile{position:absolute !important;right:8px;top:8px;z-index:2;background:color-mix(in srgb,var(--hk-surface) 90%,transparent) !important;backdrop-filter:blur(6px);box-shadow:0 1px 3px color-mix(in srgb,var(--hk-ink) 18%,transparent)}
.bc-add{gap:4px}
.bc-added{display:inline-flex;align-items:center;gap:5px;padding:3px 8px;border-radius:999px;font-size:11.5px;font-weight:600;color:var(--hk-good);background:color-mix(in srgb,var(--hk-surface) 92%,transparent);border:1px solid color-mix(in srgb,currentColor 30%,transparent);white-space:nowrap}
.bc-notice-action{margin-top:6px}
.bc-added.bc-add-tile{max-width:calc(100% - 16px);white-space:normal;line-height:1.25;border-radius:8px}
.bc-addhead{display:flex;flex-direction:row;gap:16px;align-items:center}
.bc-addhead>.bc-face{flex:none}
.bc-addgrid{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:18px;align-items:start}
.bc-add-side{position:sticky;top:96px}
@container (max-width:900px){.bc-addgrid{grid-template-columns:1fr}.bc-add-side{position:static}}
.bc-check{display:flex;gap:8px;align-items:flex-start;font-size:12.5px;line-height:1.45;cursor:pointer}
.bc-check input{margin-top:2px;accent-color:var(--hk-accent);flex:none}
.bc-soul-edit{font-family:var(--hk-mono) !important;font-size:12px !important;line-height:1.5 !important}
.bc-problems{display:grid;gap:4px}
.bc-removebox{gap:8px}
.bc-steplist{list-style:none;margin:0;padding:0;display:grid}
.bc-steplist li{display:grid;grid-template-columns:30px minmax(0,1fr);gap:10px;padding:9px 10px 9px 0;border-top:1px solid var(--hk-stroke)}
.bc-steplist li:first-child{border-top:0}
.bc-steplist li[data-you]{box-shadow:inset 3px 0 0 var(--bc-brass-fill);padding-left:10px}
.bc-steplist-n{width:24px;height:24px;border-radius:50%;display:grid;place-items:center;font-size:11px;font-weight:700;border:1.5px solid var(--bc-brass-line);background:var(--hk-surface)}
.bc-steplist-copy{display:grid;gap:3px;font-size:12.5px;justify-items:start}
.bc-steplist-owner{color:var(--bc-brass);font-weight:600}
.bc-crewbots{list-style:none;margin:0;padding:0;display:grid;gap:10px}
.bc-crewbot{display:grid;gap:10px;padding:12px 14px;border:1px solid var(--hk-stroke);border-radius:var(--hk-radius);background:var(--hk-surface)}
.bc-crewbot[data-mode=existing]{box-shadow:inset 3px 0 0 var(--bc-brass-fill)}
.bc-crewbot-head{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.bc-crewbot-head>.hk-stack{flex:1;min-width:200px}
.bc-crewbot-body{display:grid;gap:8px;padding-left:56px}
@container (max-width:700px){.bc-crewbot-body{padding-left:0}}
.bc-progress{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.bc-progress li{display:flex;gap:10px;align-items:center}
.bc-progress-icon{flex:none}
.bc-progress li[data-state=made] .bc-progress-icon,.bc-progress li[data-state=reused] .bc-progress-icon{color:var(--hk-good)}
.bc-progress li[data-state=failed] .bc-progress-icon{color:var(--hk-bad)}
.bc-cardlist{margin:0;padding-left:20px;display:grid;gap:3px;font-size:12.5px}
.bc-cardsplan{gap:8px}
.bc-minegrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,360px),1fr));gap:12px;align-items:start}
.bc-mine{gap:8px}
.bc-mine-head{display:flex;gap:10px;align-items:center}
.bc-mine-where{font-size:11px;word-break:break-all;margin:0}
.bc-crewchips{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:6px 18px}
.bc-crewchips li{display:flex;gap:8px;align-items:center}
.bc-crewchips li>span:nth-child(2){display:grid;gap:0}
@container (max-width:900px){.bc-tab .codicon{display:none}.bc-tab{padding:7px 8px 9px;font-size:12px}.bc-tabs{padding:0 12px}}
@container (max-width:600px){.bc-tabs{flex-wrap:wrap;row-gap:0}.bc-top-row{padding:8px 14px 4px}.bc-here{display:none}}
@container (max-width:820px){.bc-side{position:static}.bc-grid2{grid-template-columns:1fr}.bc-tagline{display:none}.bc-hero-scrim{background:linear-gradient(90deg,var(--hk-bg) 0%,color-mix(in srgb,var(--hk-bg) 82%,transparent) 70%,color-mix(in srgb,var(--hk-bg) 50%,transparent) 100%)}}
`

function injectStyles(ctx) {
  // The kit's sheet and this one, each a single shared <style>, removed through ctx.onDispose.
  return installStyles(ctx, PLUGIN_ID, CSS)
}

export default {
  id: PLUGIN_ID,
  register(ctx) {
    api = ctx
    injectStyles(ctx)
    const saved = recall('answers', null)
    if (saved && typeof saved === 'object') $answers.set(saved)
    const job = recall('lab-job', '')
    if (job) $lab.set({ job, plan: null, kept: {} })
    const last = recall('view', null)
    if (last && typeof last === 'object' && SECTION_OF[last.name]) $view.set({ name: last.name, id: last.id })
    const open = name => () => { go({ name }); host.navigate(ROUTE) }
    registerPage(ctx, {
      id: PLUGIN_ID, route: ROUTE, label: 'Cabinet', codicon: 'archive',
      render: () => jsx(Page, {}),
      palette: [
        { id: 'bot-cabinet-v2.open', label: 'Cabinet: find the right setup for a job', run: open('fit') },
        { id: 'bot-cabinet-v2.lab', contributionId: 'lab', label: 'Cabinet: plan a new Bot', run: open('lab') },
        { id: 'bot-cabinet-v2.cabinet', contributionId: 'cabinet', label: 'Cabinet: browse the Bots', run: open('cabinet') },
        { id: 'bot-cabinet-v2.crews', contributionId: 'crews', label: 'Cabinet: see the Crew Kits', run: open('crews') },
        { id: 'bot-cabinet-v2.mybots', contributionId: 'mybots', label: 'Cabinet: see the bots I added', run: open('mybots') },
        { id: 'bot-cabinet-v2.records', contributionId: 'records', label: 'Cabinet: read the Test Records', run: open('records') },
      ],
      keybinds: [
        { id: 'bot-cabinet-v2.open', label: 'Cabinet: find the right setup for a job', run: open('fit') },
        { id: 'bot-cabinet-v2.lab', label: 'Cabinet: plan a new Bot', run: open('lab') },
        { id: 'bot-cabinet-v2.cabinet', label: 'Cabinet: browse the Bots', run: open('cabinet') },
      ],
    })
  },
}
