import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class BrokenPipe(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX pipes")
    def test_a_reader_that_closes_early_gets_no_traceback(self):
        for args in (["--help"], ["queue", "--help"], ["guide", "--help"]):
            with self.subTest(args=args):
                self.closed_reader(args)

    def closed_reader(self, args):
        r, w = os.pipe()
        os.close(r)  # the reader is gone before pai writes a byte
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "bin", "pai.py")] + args, stdout=w,
                           stderr=subprocess.PIPE, text=True, timeout=60)
        os.close(w)
        self.assertNotIn("Traceback", p.stderr)
        self.assertNotIn("BrokenPipeError", p.stderr)
        self.assertEqual(p.returncode, 141, p.stderr)


if __name__ == "__main__":
    unittest.main()
