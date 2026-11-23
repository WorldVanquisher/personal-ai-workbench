"""A YAML check of .github/workflows/ci.yml without a YAML library (none is installed offline;
R6-COR-054). A strict parser for the block subset the workflow uses: mappings, `- ` lists, `|`
block scalars, flow lists and plain or quoted scalars. It fails on tabs, odd indentation, duplicate
keys and lines it cannot place, then the test checks the structure the core lane added (the
workbench job, the headless step, the doc_check step). GitHub validates the full schema on push."""

import re
import unittest
from pathlib import Path

CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


class YamlError(Exception):
    pass


def _scalar(v):
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        return [_scalar(x) for x in v[1:-1].split(",") if x.strip()]
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def parse(text):
    lines = []
    for n, raw in enumerate(text.split("\n"), 1):
        if "\t" in raw:
            raise YamlError("line %d: tab" % n)
        s = raw.rstrip()
        if not s.strip() or s.strip().startswith("#"):
            lines.append((n, None, s))
            continue
        lines.append((n, len(s) - len(s.lstrip(" ")), s.strip()))
    pos = [0]

    def skip():
        while pos[0] < len(lines) and lines[pos[0]][1] is None:
            pos[0] += 1

    def block_scalar(indent):
        out = []
        while pos[0] < len(lines):
            n, ind, s = lines[pos[0]]
            if ind is not None and ind <= indent:
                break
            out.append(s)
            pos[0] += 1
        return "\n".join(out)

    def node(indent):
        skip()
        if pos[0] >= len(lines):
            return None
        n, ind, s = lines[pos[0]]
        if ind < indent:
            return None
        if s.startswith("- ") or s == "-":
            return seq(ind)
        return mapping(ind)

    def entry(ind, s, n, out):
        key, sep, val = s.partition(":")
        if not sep or not re.fullmatch(r"[A-Za-z0-9_.${}\- ]+", key) or (val and not val.startswith(" ")):
            raise YamlError("line %d: not a `key: value` line: %r" % (n, s))
        key = key.strip()
        if key in out:
            raise YamlError("line %d: duplicate key %r" % (n, key))
        val = val.split(" #", 1)[0].strip() if not val.strip().startswith(("'", '"')) else val.strip()
        pos[0] += 1
        if val in ("|", ">", "|-", ">-"):
            out[key] = block_scalar(ind)
        elif val == "":
            out[key] = node(ind + 1)
        else:
            out[key] = _scalar(val)

    def mapping(indent):
        out = {}
        while True:
            skip()
            if pos[0] >= len(lines):
                return out
            n, ind, s = lines[pos[0]]
            if ind < indent:
                return out
            if ind > indent or s.startswith("- "):
                raise YamlError("line %d: unexpected indentation" % n)
            entry(ind, s, n, out)

    def seq(indent):
        out = []
        while True:
            skip()
            if pos[0] >= len(lines):
                return out
            n, ind, s = lines[pos[0]]
            if ind < indent or not (s.startswith("- ") or s == "-"):
                if ind > indent:
                    raise YamlError("line %d: unexpected indentation" % n)
                return out
            if ind != indent:
                raise YamlError("line %d: list item out of line" % n)
            rest = s[2:].strip()
            if ":" in rest and not rest.startswith(("'", '"')) and re.match(r"[A-Za-z0-9_.\-]+:( |$)", rest):
                item = {}
                lines[pos[0]] = (n, ind + 2, rest)  # the first key sits at the item's indentation
                while True:
                    skip()
                    if pos[0] >= len(lines):
                        break
                    n2, ind2, s2 = lines[pos[0]]
                    if ind2 != ind + 2 or s2.startswith("- "):
                        if ind2 > ind + 2:
                            raise YamlError("line %d: unexpected indentation" % n2)
                        break
                    entry(ind2, s2, n2, item)
                out.append(item)
            else:
                pos[0] += 1
                out.append(_scalar(rest))

    doc = node(0)
    skip()
    if pos[0] < len(lines):
        raise YamlError("line %d: could not be placed" % lines[pos[0]][0])
    return doc


class Workflow(unittest.TestCase):
    def setUp(self):
        self.doc = parse(CI.read_text(encoding="utf-8"))

    def test_parses_and_has_the_jobs(self):
        self.assertEqual(set(self.doc["jobs"]), {"test", "workbench", "userspace"})
        self.assertEqual(self.doc["permissions"], {"contents": "read"})

    def test_workbench_job(self):
        job = self.doc["jobs"]["workbench"]
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertEqual(job["strategy"]["matrix"]["python"], ["3.11", "3.13"])
        self.assertTrue(job["env"]["GIT_AUTHOR_EMAIL"].endswith("example.invalid"))
        runs = {s.get("name"): s.get("run", "") for s in job["steps"]}
        self.assertIn("strict_unittest.py swarm/tests --jobs 4", runs["Workbench suite (swarm/tests, strict)"])
        self.assertIn("bash swarm/tests/run_tests.sh", runs["Headless end-to-end (fake claude)"])

    def test_doc_check_step(self):  # D-DOC-3, R6-COR-008/054
        steps = {s.get("name"): s for s in self.doc["jobs"]["test"]["steps"]}
        step = steps["Manuals match the commands (doc_check)"]
        self.assertEqual(step["if"], "runner.os == 'Linux'")
        self.assertIn("python -B tools/doc_check.py", step["run"])

    def test_the_parser_rejects_broken_yaml(self):
        for bad in ("a: 1\n\tb: 2\n", "a: 1\na: 2\n", "a:\n  b: 1\n   c: 2\n", "jobs:\n  x:\n    - a\n   - b\n"):
            with self.subTest(bad=bad), self.assertRaises(YamlError):
                parse(bad)


if __name__ == "__main__":
    unittest.main()
