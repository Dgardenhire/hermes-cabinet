# The catalog file botcabinet.com publishes

Cabinet, the Hermes plugin, draws its Bots and Crew Kits from botcabinet.com. When it opens, and whenever
you press Refresh, it asks for one file:

    https://botcabinet.com/catalog.json

That file does not exist yet (it answers 404 today), so Cabinet shows its saved copy from October 2 and says
so. Publishing this file is all it takes to make Cabinet current. This page is the contract: what the file
must be, so that Cabinet accepts it.

The file is generated, not hand-written. From this plugin's folder:

    /usr/bin/python3 tools/make_site_catalog.py --out public/catalog.json

Copy the result into the website repository's `public/` folder (so Next.js serves it at `/catalog.json`).
`tools/make_site_catalog.py --check <file>` tells you, in plain words, everything Cabinet would refuse in
a file. To keep the file current, regenerate it whenever a Bot or Crew Kit changes, from the site's own
data (`tools/make_catalog.py <site-checkout>` takes the snapshot, `make_site_catalog.py` checks and writes it).

## How Cabinet treats it

- It fetches over `https` from `botcabinet.com` only. It follows a redirect only to the same host over
  `https`, at most three times. Any other redirect is refused.
- It waits up to four seconds, reads at most 2 MB, and needs the content type `application/json`. Serve it
  with that header (Vercel does for a `.json` file in `public/`).
- It checks every field against the rules below. One wrong field and the whole file is refused, and Cabinet
  keeps showing its last good copy. It never half-reads a file.
- It keeps the last good copy in its data folder and uses it when the site can't be reached. A file with a
  `generatedAt` older than the saved copy is refused, so an old file can't replace a new one.
- It shows "Updated Oct 4 from botcabinet.com" when it used the live file, and "Showing the saved copy from
  Oct 2" when it didn't. While the file answers 404 it adds a quiet note ("botcabinet.com doesn't publish a live
  catalog yet"), not an error; any other problem is shown as a warning that says the saved copy was kept.

## Strict means strict

- Every field below has a type and a length limit. Text with control characters or invisible
  direction-changing characters is refused, not cleaned.
- A key not listed here is refused. When the site needs a new field, raise `schemaVersion`. Cabinet then ships
  support for the new number before the site publishes it. Until it does, Cabinet keeps its saved copy.
- Slugs are lowercase letters, numbers and dashes, 1 to 64 characters, and unique within their list.
- Every `href` of a Bot or Crew Kit is an `https` address on `botcabinet.com`. Credit links may point to
  any `https` address, with no login in it.
- A Crew Kit's members, and a job guide's Bots, must be Bots in the same file.

Cabinet builds a new Hermes profile from a Bot entry and nothing else. The instructions it writes come
from `role`, `inputs`, `outputs`, `approvals`, `offLimits`, `skills`, `firstMission`, `routine` and `credits`,
word for word. It never sets a model, a tool, a schedule or a connection from this file.

## The file

Top level, all required:

| Field | Type | Rule |
|---|---|---|
| `schemaVersion` | number | exactly `2` |
| `generatedAt` | text | a real date, `2026-10-04` |
| `source` | text | up to 200 characters, who published it |
| `siteCommit` | text | up to 80 characters, the site version the file was built from |
| `origin` | text | exactly `https://botcabinet.com` |
| `bots` | list | 1 to 300 Bots |
| `crews` | list | 0 to 100 Crew Kits |
| `jobs` | list | 0 to 300 job guides |

A Bot (`bots[]`). Required: `slug`, `name`, `title`, `category`, `summary`, `outcome`.

| Field | Type | Rule |
|---|---|---|
| `slug` | text | the slug rule above |
| `name` | text | up to 60 characters |
| `title` | text | up to 120 characters |
| `category` | text | lowercase word, for example `writing`, `research`, `planning`, `technical`, `learning` |
| `summary` | text | up to 400 characters |
| `outcome` | text | up to 500 characters, the one result the Bot owns |
| `audience` | text | up to 400 characters |
| `inputs` | list of text | up to 12 items of 300 characters: what the Bot needs from you |
| `outputs` | list of text | up to 12 items of 300 characters: what it hands back |
| `examples` | list of text | up to 8 items of 300 characters |
| `firstMission` | text | up to 500 characters, the small first test |
| `approvals` | list of text | up to 20 items: what it must ask before doing |
| `offLimits` | list of text | up to 20 items: what it must never do |
| `worksWith` | list of slugs | up to 20 |
| `role` | text | up to 4,000 characters, the Bot's standing instructions (newlines allowed). Written into its SOUL.md word for word |
| `skills` | list | up to 8 of `{name, whenToUse, steps}`: `name` up to 80, `whenToUse` up to 400, `steps` up to 12 items of 700 characters |
| `routine` | object or `null` | `{trigger, activation}`: `trigger` up to 400 characters, `activation` a lowercase word such as `manual-test-required`. Shown to the owner, never turned on |
| `riskLevel` | text | up to 30 characters, for example `Moderate` or `Elevated` |
| `credits` | list | up to 4 of `{line, href}`: `line` up to 240 characters. Shown wherever the Bot appears and written into its instructions |
| `href` | text | the Bot's page, `https://botcabinet.com/bots/<slug>/` |
| `test` | object or `null` | `{status, line}`: `status` a lowercase word such as `tested` or `not-tested`, `line` up to 300 characters |

A Crew Kit (`crews[]`). Required: `slug`, `name`, `summary`, `members`, `handoffOrder`, `approvalActions`,
`prohibitedActions`, `testStatus`. Optional: `href`.

| Field | Type | Rule |
|---|---|---|
| `members` | list | 1 to 12 of `{slug, name, responsibility}`: `slug` a Bot in this file, `responsibility` up to 400 characters |
| `handoffOrder` | list | 1 to 20 of `{timing, action, owner}`: `timing` up to 80, `action` up to 400, `owner` up to 120. An owner that includes the word "person" is a point where the crew stops for you |
| `approvalActions` | list of text | up to 20: what needs your yes |
| `prohibitedActions` | list of text | up to 20 |
| `testStatus` | text | lowercase word, for example `not-tested`, `passed`, `failed-acceptance` |

A job guide (`jobs[]`), all required: `slug`, `title` (up to 120), `outcome` (up to 500), `botSlugs` (up to
12 Bot slugs), `optionalBotSlugs` (up to 12), `firstTest` (up to 500).

## A complete small example

This file passes the checker. Cabinet's tests run exactly this block.

```json
{
 "schemaVersion": 2,
 "generatedAt": "2026-10-04",
 "source": "botcabinet.com",
 "siteCommit": "a5d37b8 2026-10-02",
 "origin": "https://botcabinet.com",
 "bots": [
  {
   "slug": "scout",
   "name": "Scout",
   "title": "Topic and opportunity finder",
   "category": "research",
   "summary": "Finds timely topics and promising opportunities in the areas you choose.",
   "outcome": "Find timely topics in the subjects I choose and return a short ranked list",
   "audience": "People who want a steady list of relevant ideas",
   "inputs": ["Your topics and audience", "A list of preferred sources"],
   "outputs": ["A short ranked list", "A link for each source"],
   "examples": ["Find five developments from the past week that matter to nonprofit leaders."],
   "firstMission": "Review five approved sources and return three ideas with working links.",
   "approvals": ["Ask before contacting anyone"],
   "offLimits": ["Do not publish"],
   "worksWith": ["writer"],
   "role": "You are Scout. Find timely topics in the subjects the owner names and return a short ranked list with a source for each. Ask before contacting anyone.",
   "skills": [{"name": "Scout core Skill", "whenToUse": "When I ask for a ranked list.", "steps": ["Confirm the topics.", "Gather sources.", "Rank and return."]}],
   "routine": {"trigger": "Run when I ask. Add a schedule only after a good first run.", "activation": "manual-test-required"},
   "riskLevel": "Moderate",
   "credits": [],
   "href": "https://botcabinet.com/bots/scout/",
   "test": {"status": "not-tested", "line": "Import checked; a full task run is still pending"}
  },
  {
   "slug": "writer",
   "name": "Writer",
   "title": "Drafting assistant",
   "category": "writing",
   "summary": "Drafts clear text from material you supply.",
   "outcome": "Draft clear text from the material I supply"
  }
 ],
 "crews": [
  {
   "slug": "newsletter-desk",
   "name": "Newsletter Desk",
   "summary": "Find a topic, draft the issue, and stop for your approval.",
   "members": [
    {"slug": "scout", "name": "Scout", "responsibility": "Find the topic."},
    {"slug": "writer", "name": "Writer", "responsibility": "Draft the issue."}
   ],
   "handoffOrder": [
    {"timing": "Find", "action": "Pick the topic and gather sources.", "owner": "Scout"},
    {"timing": "Draft", "action": "Write the issue.", "owner": "Writer"},
    {"timing": "Approve", "action": "Read it and say yes or no.", "owner": "Person"}
   ],
   "approvalActions": ["Send the issue"],
   "prohibitedActions": ["Publish without approval"],
   "testStatus": "not-tested",
   "href": "https://botcabinet.com/crew-kits/newsletter-desk/"
  }
 ],
 "jobs": [
  {
   "slug": "weekly-newsletter",
   "title": "Weekly newsletter",
   "outcome": "A draft issue each week, ready for review.",
   "botSlugs": ["scout"],
   "optionalBotSlugs": ["writer"],
   "firstTest": "Draft one issue from fictional notes and check every link."
  }
 ]
}
```

## If something goes wrong

- Cabinet says "botcabinet.com hasn't published its catalog file yet": the file isn't at that address.
- Cabinet says the catalog "didn't pass Cabinet's checks": run `tools/make_site_catalog.py --check` on the
  file you published. It lists every problem.
- Cabinet keeps showing "the saved copy": it couldn't use the live file. The reason is on the Cabinet page
  under the date.

## Credit

The catalog is Bot Cabinet's own content (botcabinet.com, MIT, Damon Gardenhire). Credits for adapted work
travel in each Bot's `credits`, and Cabinet shows and writes them wherever that Bot appears.
