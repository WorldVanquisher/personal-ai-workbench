"""Portable task cards (1.0 readiness 031-040): a versioned format with description and acceptance
criteria, export with relative references, an import preview (create / unchanged / CONFLICT),
no overwrite, UTF-8 Chinese and French, path and version validation, private fields kept apart,
credential warnings, deterministic output, and a task queue recreated from exported cards."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from test_workbench import WB, needs_311  # noqa: E402

sys.path.insert(0, helpers.SWARM)
import cards  # noqa: E402
import notes  # noqa: E402

CHINESE = "修复导出文件中的中文标题"
FRENCH = "Corriger l'export : résumé, élève, « guillemets » et cœur"


def note(folder, name, text):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name + ".md"), "w", encoding="utf-8") as f:
        f.write(text)


class Format(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pai-cards-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tasks = os.path.join(self.tmp, "tasks")
        note(self.tasks, "add-json-flag", "---\nstatus: todo\npriority: 2\npaths: [src/export.py]\nworker: a\n"
             "rc_run: 20261119-1\nbranch: pai/x/a\nowner: Ada Example\n---\n# Add a --json flag\n\nWrite JSON when "
             "asked.\n\n## Acceptance criteria\n\n- `make test` passes\n- [ ] the README shows `--json`\n\n## Notes\n\n"
             "Ask before renaming.\n")
        note(self.tasks, "fix-titles", "---\nstatus: todo\npriority: 1\n---\n# %s\n\n%s\n\n## Done when\n\n1. 中文标题"
             "保持不变\n2. Le résumé reste lisible\n" % (CHINESE, FRENCH))
        note(self.tasks, "done-already", "---\nstatus: done\n---\n# Finished work\n")

    def doc(self, **kw):
        return cards.export(self.tasks, **kw)

    def test_a_card_has_description_and_acceptance_criteria(self):
        doc, withheld = self.doc()
        self.assertEqual((doc["format"], doc["version"]), ("pai-task-cards", 1))
        c = {x["name"]: x for x in doc["cards"]}["add-json-flag"]
        self.assertEqual(c["title"], "Add a --json flag")
        self.assertEqual(c["acceptance"], ["`make test` passes", "the README shows `--json`"])
        self.assertEqual(c["description"], ["Write JSON when asked.", "", "## Notes", "", "Ask before renaming."])
        self.assertEqual((c["status"], c["priority"], c["paths"]), ("todo", 2, ["src/export.py"]))
        self.assertEqual(withheld, ["branch", "owner", "rc_run", "worker"])

    def test_other_front_matter_travels_in_fields(self):
        # review keys (the review template's checks, acceptance, pr_title) and any other plain key
        note(self.tasks, "reviewed", "---\nstatus: todo\npriority: 3\nchecks: [unit, lint]\npr_title: \"feat: x\"\n"
             "acceptance: [the page works offline]\nreview: checks-passed\n---\n# Reviewed task\n\nBody.\n")
        doc, withheld = cards.export(self.tasks, names=["reviewed"])
        c = doc["cards"][0]
        self.assertEqual(c["fields"], {"acceptance": ["the page works offline"], "checks": ["unit", "lint"],
                                       "pr_title": "feat: x"})
        self.assertEqual(withheld, ["review"], "a run's review result is private, not a field")
        other = os.path.join(self.tmp, "fields")
        os.makedirs(other)
        for state, card, path in cards.import_plan(cards.load(cards.dump(doc)), other):
            with open(path, "w", encoding="utf-8") as f:
                f.write(cards.to_note(card))
        t = notes.Task(os.path.join(other, "reviewed.md"))
        self.assertEqual((t.meta["checks"], t.meta["pr_title"], t.meta["acceptance"]),
                         (["unit", "lint"], "feat: x", ["the page works offline"]))
        self.assertEqual(cards.dump(cards.export(other, names=["reviewed"])[0]), cards.dump(doc), "round trip")
        bad = json.loads(cards.dump(doc))
        bad["cards"][0]["fields"] = {"owner": "Ada"}
        with self.assertRaises(cards.CardError):
            cards.load(json.dumps(bad))

    def test_private_fields_are_grouped_apart_and_left_out_by_default(self):
        doc, _ = self.doc()
        text = cards.dump(doc)
        for v in ("Ada Example", "20261119-1", "pai/x/a"):
            self.assertNotIn(v, text)
        self.assertNotIn("private", doc)
        doc, withheld = self.doc(include_private=True)
        self.assertEqual(withheld, [])
        self.assertEqual(doc["private"], {"add-json-flag": {"branch": "pai/x/a", "owner": "Ada Example",
                                                            "rc_run": "20261119-1", "worker": "a"}})
        self.assertNotIn("owner", {k for c in doc["cards"] for k in c}, "never mixed into a card")

    def test_review_sections_from_pr_check_stay_private(self):
        """`pai pr check` appends a section with the worktree path and the check commands: run-specific."""
        note(self.tasks, "reviewed", "---\nstatus: todo\nreview: checks-passed\n---\n# Reviewed\n\nDo it.\n\n"
             "## Review 2026-11-19T10:00:00Z · checks passed\n- remote origin, task branch `pai/r/a`, worktree "
             "`/home/ada/wt/a`\n- check `unit`: PASSED (exit 0, 0.1s) `/usr/bin/python3 -m unittest`\n\n"
             "## Acceptance criteria\n\n- tests pass\n")
        doc, withheld = self.doc(names=["reviewed"])
        text = cards.dump(doc)
        self.assertNotIn("/home/ada/wt/a", text)
        self.assertNotIn("/usr/bin/python3", text)
        self.assertEqual(doc["cards"][0]["description"], ["Do it."])
        self.assertEqual(doc["cards"][0]["acceptance"], ["tests pass"], "the sections after it are kept")
        self.assertIn("review sections written by `pai pr check` (worktree path, check commands)", withheld)
        doc, _ = self.doc(names=["reviewed"], include_private=True)
        self.assertIn("worktree `/home/ada/wt/a`", " ".join(doc["private"]["reviewed"]["review_sections"]))

    def test_canonical_output_is_deterministic(self):
        a = cards.dump(self.doc()[0])
        b = cards.dump(self.doc()[0])
        self.assertEqual(a, b)
        self.assertTrue(a.endswith("\n"))
        names = [c["name"] for c in json.loads(a)["cards"]]
        self.assertEqual(names, sorted(names))
        self.assertIn('"Write JSON when asked."', a.splitlines()[a.splitlines().index('      "description": [') + 1])
        self.assertIn(CHINESE, a, "non-ASCII text is kept as it is, not escaped")

    def test_utf8_round_trip_with_chinese_and_french(self):
        doc, _ = self.doc()
        text = cards.dump(doc)
        back = cards.load(text)
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        for state, c, path in cards.import_plan(back, other):
            self.assertEqual(state, "create")
            with open(path, "w", encoding="utf-8") as f:
                f.write(cards.to_note(c))
        t = notes.Task(os.path.join(other, "fix-titles.md"))
        self.assertEqual(t.title, CHINESE)
        self.assertIn(FRENCH, t.body)
        again, _ = cards.export(other)
        self.assertEqual(cards.dump(again), text, "notes made from cards export to the same bytes")

    def test_relative_references_only(self):
        doc, _ = cards.export(self.tasks, project="demo", repo=os.path.join(self.tmp, "repo"),
                              out_dir=os.path.join(self.tmp, "share", "cards"))
        self.assertEqual(doc["project"], {"name": "demo", "repository": "../../repo"})

    def test_validation(self):
        good = cards.dump(self.doc()[0])

        def bad(edit, needle):
            d = json.loads(good)
            edit(d)
            with self.assertRaises(cards.CardError) as cm:
                cards.load(json.dumps(d))
            self.assertIn(needle, str(cm.exception))

        bad(lambda d: d.update(version=2), "unsupported format version 2")
        bad(lambda d: d.update(format="other"), "not a pai-task-cards file")
        bad(lambda d: d["cards"][0].update(paths=["/etc/passwd"]), "is absolute")
        bad(lambda d: d["cards"][0].update(paths=["C:/Users/x"]), "is absolute")
        bad(lambda d: d["cards"][0].update(paths=["src/../../outside"]), "leaves the repository")
        bad(lambda d: d["cards"][0].update(paths=["src\\win.py"]), "backslash")
        bad(lambda d: d["cards"][0].update(name="../escape"), "cannot be a note file name")
        bad(lambda d: d["cards"][0].update(name=".hidden"), "cannot be a note file name")
        bad(lambda d: d["cards"][0].update(extra=1), "unknown field")
        bad(lambda d: d["cards"].append(dict(d["cards"][0])), "appears twice")
        bad(lambda d: d.update(project={"repository": "/home/someone/repo"}), "must be relative")
        self.assertEqual(cards.check_rel("src/./a/../b.py", "x"), "src/b.py")

    def test_credentials_are_flagged_and_masked(self):
        token = "ghp_" + "A1b2C3d4" * 5
        note(self.tasks, "leaky", "---\nstatus: todo\n---\n# Deploy\n\nUse %s and\npassword = hunter2hunter2\n"
             "and https://bob:pa55word@example.invalid/x\n" % token)
        doc, _ = self.doc()
        hits = cards.flagged(doc)
        kinds = sorted((h[0], h[1], h[2], h[3]) for h in hits)
        self.assertEqual(kinds, [("leaky", "description", 1, "GitHub token"), ("leaky", "description", 2,
                                                                               "secret assignment"),
                                 ("leaky", "description", 3, "password in a URL")])
        masked = cards.mask(cards.dump(doc), hits)
        self.assertNotIn(token, masked)
        self.assertNotIn("pa55word", masked)
        self.assertEqual(cards.flagged(self.doc(names=["fix-titles"])[0]), [], "ordinary text is not flagged")


@helpers.needs_git
@needs_311
class Commands(WB):
    def setUp(self):
        super().setUp()
        for name, prio, status in (("alpha", 3, "todo"), ("beta", 5, "todo"), ("gamma", 1, "todo"), ("old", 9, "done")):
            self.note(name, status=status, priority=prio, body="Body of %s.\n\n## Acceptance criteria\n\n- %s works"
                      % (name, name))
        note(self.tasks, "chinese", "---\nstatus: todo\npriority: 4\n---\n# %s\n\n%s\n" % (CHINESE, FRENCH))
        self.init()

    def test_export_preview_write_and_import(self):
        p = self.pai("task", "export")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PREVIEW (nothing written): 5 card(s)", p.stdout)
        self.assertIn(CHINESE, p.stdout)
        out = os.path.join(self.tmp, "share", "cards.json")
        os.makedirs(os.path.dirname(out))
        self.assert_ok(self.pai("task", "export", "--out", out))
        with open(out, encoding="utf-8") as f:
            first = f.read()
        self.assertEqual(json.loads(first)["project"]["repository"], os.path.relpath(self.repo, os.path.dirname(out)))
        p = self.pai("task", "export", "--out", out)
        self.assertEqual(p.returncode, 1, "an existing export file is never overwritten")
        with open(out, encoding="utf-8") as f:
            self.assertEqual(f.read(), first)

        # A second project with an empty task folder recreates the same queue.
        before = self.pai("task", "list").stdout
        order = [l.split()[2] for l in before.splitlines() if l.startswith("  todo")]
        fresh = os.path.join(self.tmp, "fresh-tasks")
        os.makedirs(fresh)
        self.set_config({("run", "tasks"): fresh})
        p = self.pai("task", "import", out)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PREVIEW (nothing written): 5 card(s)", p.stdout)
        self.assertEqual(os.listdir(fresh), [], "the preview wrote nothing")
        self.assert_ok(self.pai("task", "import", out, "--write"))
        after = self.pai("task", "list").stdout
        self.assertEqual([l.split()[2] for l in after.splitlines() if l.startswith("  todo")], order)
        self.assertIn("  done", after)
        self.assert_ok(self.pai("task", "export", "--out", out + ".2"))
        with open(out + ".2", encoding="utf-8") as f:
            self.assertEqual(json.loads(f.read())["cards"], json.loads(first)["cards"], "same cards from the recreated queue")

        # Importing again: unchanged; a changed note is a conflict, named by path, never overwritten.
        p = self.pai("task", "import", out)
        self.assertEqual(p.stdout.count("unchanged"), 5, p.stdout)
        with open(os.path.join(fresh, "beta.md"), "a", encoding="utf-8") as f:
            f.write("\nmy local edit\n")
        p = self.pai("task", "import", out, "--write")
        self.assertEqual(p.returncode, 1)
        self.assertIn("CONFLICT %s exists with different content" % os.path.join(fresh, "beta.md"), p.stdout)
        with open(os.path.join(fresh, "beta.md"), encoding="utf-8") as f:
            self.assertIn("my local edit", f.read())

    def test_refusals(self):
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w") as f:
            json.dump({"format": "pai-task-cards", "version": 9, "cards": []}, f)
        p = self.pai("task", "import", bad)
        self.assertEqual(p.returncode, 2)
        self.assertIn("unsupported format version 9", p.stdout)
        self.note("secret", body="token: ghp_%s" % ("Z9y8X7w6" * 5))
        p = self.pai("task", "export", "secret")
        self.assertEqual(p.returncode, 1)
        self.assertIn("WARNING  card secret, description line 1: looks like a GitHub token", p.stdout)
        self.assertNotIn("Z9y8X7w6Z9y8", p.stdout)
        out = os.path.join(self.tmp, "s.json")
        p = self.pai("task", "export", "secret", "--out", out)
        self.assertEqual(p.returncode, 1)
        self.assertFalse(os.path.exists(out), "flagged text is not written without --allow-flagged")
        self.assert_ok(self.pai("task", "export", "secret", "--out", out, "--allow-flagged"))


if __name__ == "__main__":
    unittest.main()
