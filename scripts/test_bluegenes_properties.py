"""Tests for bluegenes-properties.py, offline: a small HTTP server stands in for
the mine and behaves like InterMine's BGPropertiesServlet (GET; POST creates
from form parameters; PUT updates, URL-decoding its body and then splitting it
on & and =, or reading query parameters when the body is empty; writes need a
superuser token).

    python3 -m unittest discover -s scripts -p 'test_*.py'
"""
import contextlib
import importlib.util
import io
import json
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("bgp", HERE / "bluegenes-properties.py")
bgp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bgp)

LAYOUT = json.loads((HERE / "testdata/humanmine-bluegenes-properties.json").read_text())[
    "bluegenes-properties"]["layout.report"]
TOKEN = "superuser-token"


class FakeMine:
    def __init__(self, props):
        self.props = dict(props)
        self.calls = []
        mine = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, status, obj=None):
                body = json.dumps(obj or {"wasSuccessful": status == 200}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def params(self, body):
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if self.command == "POST":
                    q.update(urllib.parse.parse_qs(body))
                return {k: v[0] for k, v in q.items()}

            def handle_write(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode()
                mine.calls.append((self.command, self.path, body))
                if self.headers.get("Authorization") != "Token " + TOKEN:
                    return self.reply(403)
                if self.command == "PUT":
                    key = value = ""
                    if body:  # BGPropertiesUpdateService.parseInput
                        for pair in urllib.parse.unquote_plus(body).split("&"):
                            kv = [x for x in pair.split("=") if x]
                            if kv and kv[0].lower() == "key":
                                key = kv[1]
                            elif kv and kv[0].lower() == "value":
                                value = kv[1]
                    if not key or not value:
                        p = self.params(body)
                        key, value = p.get("key"), p.get("value")
                    if not key or not value:
                        return self.reply(400)
                    if key not in mine.props:
                        return self.reply(404)
                else:
                    p = self.params(body)
                    key, value = p.get("key"), p.get("value")
                    if key in mine.props:
                        return self.reply(400)
                mine.props[key] = value
                self.reply(200)

            def do_GET(self):
                self.reply(200, {"bluegenes-properties": mine.props, "wasSuccessful": True})

            do_POST = do_PUT = handle_write

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.root = f"http://127.0.0.1:{self.server.server_port}/testmine"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            code = bgp.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


class Format(unittest.TestCase):
    def test_pretty_is_the_same_value_one_item_per_line(self):
        p = bgp.pretty(LAYOUT)
        self.assertEqual(bgp.compact(p), LAYOUT)
        self.assertGreater(p.count("\n"), 40)
        self.assertIn('\n        {:label "JBrowse 2 Genome Browser"', p)

    def test_compact_keeps_strings_intact(self):
        edn = '{"A b" [{:label "x  [ {y} ]\\" z", :v nil}]}'
        self.assertEqual(bgp.compact(bgp.pretty(edn)), edn)
        self.assertEqual(bgp.compact('{"A b"\n   [ {:l "s"}\n ] }'), '{"A b" [{:l "s"}]}')

    def test_file_names(self):
        self.assertEqual(bgp.key_for(Path("x/layout.report.edn")), "layout.report")
        self.assertEqual(bgp.key_for(Path("notice.md")), "notice")
        self.assertEqual(bgp.file_for("layout.report"), "layout.report.edn")
        self.assertEqual(bgp.service_url("https://m.org/humanmine/"),
                         "https://m.org/humanmine/service/bluegenes-properties")


class Commands(unittest.TestCase):
    def setUp(self):
        self.mine = FakeMine({"layout.report": LAYOUT})
        self.tmp = Path(tempfile.mkdtemp())
        self.token = self.tmp / "token"
        self.token.write_text(TOKEN + "\n")

    def tearDown(self):
        self.mine.close()

    def apply(self, *files, extra=()):
        return run("apply", self.mine.root, *map(str, files), "--token-file", str(self.token),
                   "--backup-dir", str(self.tmp / "backups"), *extra)

    def test_export_then_check_matches(self):
        code, _ = run("export", self.mine.root, "-o", str(self.tmp))
        self.assertEqual(code, 0)
        f = self.tmp / "layout.report.edn"
        self.assertEqual(bgp.compact(f.read_text()), LAYOUT)
        self.assertEqual(run("check", self.mine.root, str(f))[0], 0)
        self.mine.props["layout.report"] = LAYOUT.replace("JBrowse 2 Genome Browser", "Other")
        code, out = run("check", self.mine.root, str(f))
        self.assertEqual(code, 1)
        self.assertIn("DIFFERS", out)

    def test_apply_restores_a_changed_layout_with_put_and_backs_up_first(self):
        run("export", self.mine.root, "-o", str(self.tmp))
        broken = LAYOUT.replace("bluegenesJBrowse2Tool", "bluegenesJBrowseTool")
        self.mine.props["layout.report"] = broken
        code, out = self.apply(self.tmp / "layout.report.edn")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.mine.props["layout.report"], LAYOUT)  # exact spelling restored
        method, path, body = self.mine.calls[-1]
        self.assertEqual(method, "PUT")
        self.assertNotIn("?", path)
        self.assertTrue(body.startswith("key=layout.report&value="))
        backups = list((self.tmp / "backups").glob("*/layout.report.edn"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(bgp.compact(backups[0].read_text()), broken)
        self.assertIn("Verified", out)

    def test_apply_is_a_no_op_when_nothing_changed(self):
        run("export", self.mine.root, "-o", str(self.tmp))
        code, out = self.apply(self.tmp / "layout.report.edn")
        self.assertEqual((code, self.mine.calls), (0, []))
        self.assertIn("Nothing to do", out)

    def test_values_with_ampersand_or_equals_go_in_the_query_string(self):
        f = self.tmp / "layout.report.edn"
        value = LAYOUT.replace("Gene Ontology", "GO & friends = fun")
        f.write_text(bgp.pretty(value))
        code, out = self.apply(f)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.mine.props["layout.report"], value)
        method, path, body = self.mine.calls[-1]
        self.assertEqual((method, body), ("PUT", ""))
        self.assertIn("?key=layout.report&value=", path)

    def test_new_key_is_created_with_post(self):
        f = self.tmp / "notice.md"
        f.write_text("### Downtime on Friday\n")
        code, out = self.apply(f)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.mine.props["notice"], "### Downtime on Friday")
        self.assertEqual(self.mine.calls[-1][0], "POST")

    def test_refusals(self):
        f = self.tmp / "layout.report.edn"
        f.write_text(bgp.pretty(LAYOUT.replace("Gene Ontology", "Changed")))
        code, _ = run("apply", self.mine.root, str(f), "--backup-dir", str(self.tmp / "b"))
        self.assertIn("token", str(code))
        self.assertEqual(self.mine.calls, [])
        code, out = self.apply(f, extra=["--dry-run"])
        self.assertEqual((code, self.mine.calls), (0, []))
        self.assertIn("Would write: layout.report", out)
        (self.tmp / "empty.edn").write_text("\n")
        bad = self.tmp / "layout.report.edn"
        bad.write_text("[not a map]")
        self.assertIn("EDN map", str(self.apply(bad)[0]))
        self.token.write_text("wrong")
        bad.write_text(bgp.pretty(LAYOUT.replace("Gene Ontology", "Changed")))
        self.assertIn("403", str(self.apply(bad)[0]))
        self.assertEqual(self.mine.props["layout.report"], LAYOUT)


if __name__ == "__main__":
    unittest.main()
