"""`pai doctor` (workbench) ends with the manual section that matches what it found (R6-DOC-027)."""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import workbench  # noqa: E402


class DoctorNext(unittest.TestCase):
    def test_each_state_names_its_section(self):
        self.assertIn('section 8 "When something goes wrong"', workbench.doctor_next({"name": "p"}, True))
        self.assertIn("section 5", workbench.doctor_next(None, False))
        # R6-FIN-030 (newbie2 F3): every Next line has a Chinese twin naming the Chinese page
        for line in (workbench.doctor_next(None, False), workbench.doctor_next({"name": "p"}, False),
                     workbench.doctor_next({"name": "p"}, True)):
            en, zh = line.split("\n")
            self.assertTrue(en.startswith("Next: ") and zh.startswith("中文："), line)
            self.assertIn("FIRST-USE.zh-CN.md 第", zh)
        line = workbench.doctor_next({"name": "p"}, False)
        self.assertIn('section 6 "Your first round"', line)
        self.assertIn("REFRESH.md, part (b)", line)

    def test_the_local_manuals_have_those_sections(self):
        with open(workbench.manual("FIRST-USE.md"), encoding="utf-8") as f:
            text = f.read()
        for h in ("## 5. Save this checkout's defaults: `pai init`", "## 6. Your first round", "## 8. When something goes wrong"):
            self.assertIn(h, text)
        with open(workbench.manual("FIRST-USE.zh-CN.md"), encoding="utf-8") as f:
            zh = f.read()
        for h in ("## 5. 保存这个仓库的默认设置", "## 6. 第一轮：一个 worker", "## 8. 出错时怎么办"):
            self.assertIn(h, zh)

    def test_an_installed_package_gets_the_github_file_for_its_version(self):
        from pai import __version__
        with mock.patch.object(workbench.os.path, "isfile", return_value=False):
            self.assertTrue(workbench.manual("FIRST-USE.md").endswith("/blob/v%s/docs/FIRST-USE.md" % __version__))


class AgentsBeforeInit(unittest.TestCase):
    """R6-FIN-020 (newbie F2-F4, decision D-FIN-2 = A): before an agent is chosen, a missing claude or codex is
    listed for the first run and does not fail doctor; once chosen, it is essential and doctor says so."""

    def rows(self, essential):
        import doctor
        have = {"git", "tmux", "ps", "npm"}
        with mock.patch.object(doctor.shutil, "which", lambda t: "/usr/bin/" + t if t in have else None), \
                mock.patch.object(doctor, "_run", lambda argv, timeout=25: (0, "x 1.0", "")):
            return doctor, doctor.check(("claude", "codex"), agents_essential=essential)

    def test_not_chosen_yet_is_later_and_not_failing(self):
        doctor, rows = self.rows(False)
        need = {r["tool"]: r["need"] for r in rows}
        self.assertEqual((need["claude"], need["codex"], need["git"]), ("later", "later", "essential"))
        self.assertEqual(doctor.failing(rows), [])
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            doctor.show(rows)
        self.assertIn("FOR YOUR FIRST RUN", out.getvalue())
        self.assertNotIn("MISSING        claude", out.getvalue().split("FOR YOUR FIRST RUN")[0])
        self.assertIn("npm install -g @openai/codex   # before pai run", out.getvalue())

    def test_chosen_agent_is_essential_and_next_names_it(self):
        doctor, rows = self.rows(True)
        failing = doctor.failing(rows)
        self.assertEqual({r["tool"] for r in failing}, {"claude", "codex"})
        line = workbench.doctor_next({"name": "p"}, failing, ("claude", "codex"))
        self.assertIn("install and log in to claude or codex", line)
        self.assertIn('section 6 "Your first round" needs it', line)
        other = [{"tool": "tmux", "state": "missing"}]
        self.assertIn('section 8 "When something goes wrong"', workbench.doctor_next({"name": "p"}, other, ("claude",)))


if __name__ == "__main__":
    unittest.main()
