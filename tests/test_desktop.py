"""desktop/plugin.js stays inside the catalog's desktop rules, and its copy follows Cabinet's rules.

desktop/plugin.js is BUILT: esbuild bundles desktop/src/plugin.js with the shared hermes-ui-kit
(`node ../hermes-ui-kit/scripts/build.mjs .`). The catalog rules read the built file, because that is
what Hermes loads. esbuild prints string literals in double quotes, so the import check accepts either
quote, and the checks about how Cabinet's copy and calls are written read the source (SRC)."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")  # the built file Hermes loads
SRC = (ROOT / "desktop" / "src" / "plugin.js").read_text(encoding="utf-8")  # the source it is built from


def strings():
    """Every single-quoted literal and template text in the page (the copy a user can read)."""
    return re.findall(r"'((?:[^'\\\n]|\\.)*)'", SRC)


class DesktopRules(unittest.TestCase):
    def test_only_sdk_and_react_imports(self):
        sources = set(re.findall(r"^import[\s\S]*?from [\"']([^\"']+)[\"']", JS, flags=re.M))
        self.assertEqual(sources, {"react", "react/jsx-runtime", "@hermes/plugin-sdk"})

    def test_no_banned_constructs(self):
        for pattern in (r"\beval\(", r"new Function\(", r"import\(", r"\.prototype\.", r"createElement\('script'\)",
                        r"innerHTML", r"dangerouslySetInnerHTML", r"(?<![A-Za-z.])fetch\(", r"localStorage"):
            self.assertIsNone(re.search(pattern, JS), pattern)

    def test_bodies_are_passed_as_objects(self):
        # ctx.rest encodes the body itself; a pre-encoded string reaches the backend as one string (422).
        self.assertNotIn("JSON.stringify", JS)

    def test_talks_only_through_ctx_rest(self):
        self.assertIn("api.rest(path", JS)

    def test_theme_variables_only(self):
        self.assertEqual(set(re.findall(r"#[0-9a-fA-F]{3,6}\b", JS)), set())

    def test_chat_buttons_send_in_a_new_chat(self):
        # A chat button opens a new chat and sends the message there; the clipboard is only the fallback.
        body = SRC[SRC.index("async function sendInNewChat"):SRC.index("// Hermes seeds a new profile")]
        self.assertIn("host.newChat()", body)
        self.assertIn("composer.submit('new', text)", body)
        self.assertIn("i < 24", body)  # about three seconds at 125 ms
        self.assertIn("pause(125)", body)
        self.assertLess(body.index("composer.submit"), body.index("writeClipboard"))
        self.assertNotIn("setDraft", SRC)
        self.assertNotIn("You send it", (ROOT / "api.py").read_text(encoding="utf-8"))

    def test_create_passes_no_model(self):
        # The one place a profile is made sends the reviewed request as it is: no model, no extra params.
        self.assertIn("host.request(request.method, request.params, CREATE_TIMEOUT_MS)", SRC)
        # The only other gateway call is the read-only Bots catalog probe.
        self.assertIn("host.request('bots.catalog', {}, PROBE_TIMEOUT_MS)", SRC)
        self.assertEqual(SRC.count("host.request("), 2)
        self.assertNotRegex(JS, r"model\s*:")

    def test_create_waits_long_enough_for_hermes(self):
        # Hermes seeds a new profile in a subprocess; the gateway's 30 s default can give up on a bot it then makes.
        self.assertIn("const CREATE_TIMEOUT_MS = 120000", SRC)


class Install(unittest.TestCase):
    def test_add_sends_the_reviewed_request_and_then_records_it(self):
        self.assertIn("createAndRecord(plan.create, plan.planId)", SRC)  # Add, and Make it from a kit
        self.assertIn("createAndRecord(check.create, check.planId)", SRC)  # Bot Lab
        self.assertIn("createProfile(m.create)", SRC)  # each new crew member
        self.assertIn("post('install/confirm', { planId, created: !failure })", SRC)

    def test_a_lost_reply_still_gets_recorded(self):
        # If profiles.create fails, the page still asks the backend to record the plan before showing an error.
        body = SRC[SRC.index("async function createAndRecord"):SRC.index("/** A backend read")]
        self.assertLess(body.index("catch (e) {\n    failure = e"), body.index("post('install/confirm'"))
        self.assertIn("if (failure) throw failure", body)
        self.assertIn("post('crews/confirm', { planId: plan.planId, created })", SRC)

    def test_a_failed_create_is_confirmed_as_not_made_by_cabinet(self):
        # The backend may only repair or adopt a profile when Cabinet's own create succeeded (created: true).
        body = SRC[SRC.index("async function createAndRecord"):SRC.index("/** A backend read")]
        self.assertIn("created: !failure", body)

    def test_a_name_hermes_already_has_is_never_confirmed(self):
        # Hermes says "A profile named 'x' already exists": nothing was made for this plan, so nothing is recorded.
        body = SRC[SRC.index("async function createAndRecord"):SRC.index("/** A backend read")]
        self.assertLess(body.index("if (failure && alreadyExists(failure)) throw failure"), body.index("post('install/confirm'"))
        self.assertIn("/already exists/i", SRC)

    def test_nothing_destructive_without_an_explicit_confirm(self):
        self.assertIn("{ confirm: true }", SRC)
        self.assertIn("disabled: !sure", SRC)  # the Kanban cards button needs the ticked box

    def test_the_page_never_names_a_model_or_credential(self):
        for word in ("api_key", "auth.json", "mirror_credentials", "share_auth"):
            self.assertNotIn(word, SRC)

    def test_every_review_screen_has_a_cancel(self):
        self.assertGreaterEqual(SRC.count("children: 'Cancel'"), 2)

    def test_the_live_catalog_is_asked_for_on_open(self):
        self.assertIn("rest('catalog')", SRC)
        self.assertIn("rest('catalog/refresh', { method: 'POST' })", SRC)

    def test_the_built_file_is_current_with_its_source(self):
        # the bundle carries the new screens
        for text in ("Set up this crew", "My Bots", "Add to my bots"):
            self.assertIn(text, JS)


class Wiring(unittest.TestCase):
    """Every backend path the page calls exists in dashboard/plugin_api.py, with the right method. No dead calls."""

    def routes(self):
        api_src = (ROOT / "dashboard" / "plugin_api.py").read_text(encoding="utf-8")
        found = re.findall(r'@router\.(get|post)\("/([^"]*)"\)', api_src)
        return {(m.upper(), re.sub(r"\{[^}]+\}", "{}", p)) for m, p in found}

    def calls(self):
        norm = lambda p: re.sub(r"\$\{[^}]+\}", "{}", p)
        out = set()
        for p in re.findall(r"\bpost\([`']([^`']+)[`']", SRC):
            out.add(("POST", norm(p)))
        for p, opts in re.findall(r"\brest\([`']([^`']+)[`'](, \{ method: 'POST' \})?", SRC):
            out.add(("POST" if opts else "GET", norm(p)))
        for p in re.findall(r"usePlan\((?:[^,?]*\? )?[`']([^`']+)[`']", SRC):
            out.add(("POST", norm(p)))
        for p in re.findall(r"useLoad\('([^']+)'\)", SRC):
            out.add(("GET", norm(p)))
        return out

    def test_every_call_has_a_route(self):
        calls = self.calls()
        self.assertGreaterEqual(len(calls), 18)
        self.assertEqual(calls - self.routes(), set())


class Copy(unittest.TestCase):
    def test_no_emoji(self):
        self.assertIsNone(re.search(r"[\U0001F300-\U0001FAFF☀-➿]", JS))
        self.assertIsNone(re.search(r"[\U0001F300-\U0001FAFF☀-➿]", SRC))

    def test_headlines_and_buttons_have_no_closing_period(self):
        heads = re.findall(r"jsx\('h[123]', \{[^}]*?children: '([^']+)'", SRC)
        buttons = re.findall(r"jsx\(Button, \{[^}]*?children: '([^']+)'", SRC)
        self.assertTrue(heads and buttons)
        for text in heads + buttons:
            self.assertFalse(text.rstrip().endswith("."), text)

    def test_my_bots_confirms_open_next_to_their_button(self):
        # Remove, Kanban cards and Make a bot from it used to open at the bottom of the page, far from the card.
        body = SRC[SRC.index("function MyBots()"):SRC.index("function Page()")]
        for rk in ("bot", "crew", "kit"):
            self.assertIn(f"panelFor('{rk}',", body)
        self.assertNotIn("open && open.kind === 'remove' ?", body)

    def test_the_fit_answer_button_always_goes_somewhere(self):
        body = SRC[SRC.index("function nextStep(out)"):SRC.index("function Answer({ out })")]
        self.assertNotIn("return null", body)
        self.assertIn("out.next.action === 'crews'", body)

    def test_a_crew_answer_offers_set_up_directly(self):
        self.assertIn("go({ name: 'setup', id: c.slug }), children: `Set up ${c.name}`", SRC)

    def test_a_failed_crew_says_so_before_set_up(self):
        self.assertIn("jsx(FailedCrewNotice, { crew: c }", SRC)
        self.assertIn("jsx(FailedCrewNotice, { crew }", SRC)

    def test_crew_counts_name_steps_not_handoffs(self):
        # Four steps make three handoffs; the count shown is the number of steps.
        self.assertNotIn("steps.length} handoffs", SRC)
        self.assertEqual(SRC.count("steps.length} steps · you step in"), 2)

    def test_crew_permissions_use_plain_words(self):
        # The site's line talks about "Passports"; inside Hermes the crew page says what Cabinet does and doesn't enforce.
        self.assertNotIn("c.permissions", SRC)
        self.assertIn("not locks", SRC)

    def test_the_ladder_and_the_answers_use_the_same_names(self):
        # Start Here and the Fit Test used to say "Assignment, Skill, Routine" beside "Ask Hermes once, Save it as a skill".
        from _load import sub
        fit = sub("fit")
        for kind in fit.KIND_ORDER:
            self.assertIn(f"{kind}: {{ title: '{fit.KINDS[kind]['title']}'", SRC)

    def test_no_teaching_terms_on_screen(self):
        visible = "\n".join(line for line in SRC.splitlines() if not line.strip().startswith("//") and not line.strip().startswith("*"))
        for word in ("'Inspection Desk", "Open the Inspection Desk", "This becomes its SOUL.md", "Standard wording", "Kanban cards…",
                     "'Copy the plan'", "How the Inspection Desk labels evidence"):
            self.assertNotIn(word, visible, word)

    def test_bot_lab_needs_no_keep_clicks(self):
        # Every line used to need its own Keep press before Create worked.
        body = SRC[SRC.index("function BotLab()"):SRC.index("/* ------------------------------------------------------------------ review screens -- */")]
        self.assertNotIn("unkept", body)
        self.assertIn("const canCreate = Boolean(check && check.ready && !check.reserved)", body)

    def test_my_bots_is_plain_for_a_new_user(self):
        body = SRC[SRC.index("function MyBots()"):SRC.index("function Page()")]
        self.assertIn("some ? 'What you added with Cabinet' : 'My Bots'", body)

    def test_no_faded_body_text(self):
        # Body text stays full contrast: no opacity on text and no muted text color.
        # (Placeholder hints inside empty inputs are the one exception; they vanish when you type.)
        css = SRC[SRC.index("const CSS"):SRC.index("function injectStyles")]
        faded = [line for line in css.splitlines() if re.search(r"(?<!-)opacity:\s*\.[0-9]", line) and "::placeholder" not in line]
        self.assertEqual(faded, [])
        self.assertNotIn("muted-foreground", css)



class HermesBots(unittest.TestCase):
    """Hermes's own Bots catalog (NousResearch/hermes-agent#119260): detected by the gateway, handed over, never duplicated."""

    def body(self, start, end):
        return SRC[SRC.index(start):SRC.index(end)]

    def test_detection_is_the_gateway_answering_bots_catalog(self):
        probe = self.body("async function probeHermesBots", "/** The title Hermes's catalog lists")
        self.assertIn("host.request('bots.catalog', {}, PROBE_TIMEOUT_MS)", probe)
        self.assertIn("Array.isArray(out.entries) || Array.isArray(out.removed)", probe)
        self.assertIn("catch (e)", probe)  # any failure: no Nous catalog
        self.assertIn("let next = { checked: true, available: false, names: {} }", probe)
        self.assertIn("probeOk ? REFRESH_AFTER_MS : PROBE_RETRY_MS", probe)  # cached: a yes for 30 min, a no for a minute
        self.assertIn("probeHermesBots(force)", self.body("async function loadCatalog", "function remember"))

    def test_hand_over_opens_hermes_bots(self):
        self.assertIn("const HERMES_BOTS_ROUTE = '/capabilities?tab=bots'", SRC)
        self.assertIn("host.navigate(HERMES_BOTS_ROUTE)", self.body("function openHermesBots", "async function loadCatalog"))

    def test_carried_bots_get_add_in_hermes_bots_and_no_cabinet_add(self):
        button = self.body("function AddButton", "function BotTile")
        self.assertIn("'Add in Hermes Bots'", button)
        self.assertIn("openHermesBots(bot, title)", button)
        self.assertLess(button.index("carriedBy(hb, bot)"), button.index("go({ name: 'add', id: bot.slug })"))
        add = self.body("function AddBot", "/* ------------------------------------------------------------------ setting up a crew")
        self.assertIn("usePlan(bot && !handOver ?", add)  # a carried Bot isn't even planned
        self.assertNotIn("anyway", SRC)
        self.assertNotIn("anyway", JS)

    def test_bots_already_added_through_hermes_get_no_second_install(self):
        self.assertIn("'Already added through Hermes Bots'", self.body("function AddButton", "function BotTile"))
        self.assertIn("Already added through Hermes Bots", self.body("function MyBots", "function Page()"))
        self.assertIn("addedBy(catalogState, bot)", self.body("function AddBot", "async function add()"))

    def test_the_notice_names_the_real_place_and_only_shows_with_the_catalog(self):
        notice = self.body("function HandoverNotice", "/* ------------------------------------------------------------------ frame")
        self.assertIn("if (!hb.available) return null", notice)
        self.assertIn("Capabilities, in the Bots tab", notice)
        self.assertIn("Browse Bot Marketplace…", notice)
        self.assertNotIn("Bots tab in the sidebar", SRC)

    def test_a_set_up_crew_points_to_a_group_chat(self):
        tip = self.body("function GroupChatTip", "function SetUpCrew")
        self.assertIn("Start a group chat with these bots", tip)
        self.assertIn("New group chat", tip)
        self.assertIn("jsx(GroupChatTip, { crew: view }, 'groupchat')", SRC)


if __name__ == "__main__":
    unittest.main()
