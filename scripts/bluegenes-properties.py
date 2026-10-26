#!/usr/bin/env python3
"""Export, check and apply a mine's BlueGenes properties.

BlueGenes keeps a few settings in the mine's userprofile database rather than
in any file: the report page layouts (`layout.report`) and the homepage notice
(`notice`). They are read and written through the web service at
`<mine>/service/bluegenes-properties`, which is what the BlueGenes Admin page
uses. This script lets them live in git instead:

    export  write each property to a file (layouts pretty-printed, one tool,
            class or template per line, so diffs are readable)
    check   compare files with the live mine; exit status 1 if they differ
    apply   write files to the mine (needs a superuser API token), after
            saving the current live values as a backup

Files are named after the key: `layout.report.edn`, `notice.md`; any other
name is used as the key as it is. Only standard-library Python 3.8+.

Examples:
    bluegenes-properties.py export https://www.humanmine.org/humanmine -o bluegenes
    bluegenes-properties.py check  https://www.humanmine.org/humanmine bluegenes/layout.report.edn
    BLUEGENES_TOKEN=... bluegenes-properties.py apply https://www.humanmine.org/humanmine \\
        bluegenes/layout.report.edn
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

EXTENSIONS = {"layout.report": ".edn", "notice": ".md"}
# Request lines longer than this risk nginx's and Tomcat's default 8 KB limits.
MAX_QUERY_BYTES = 7000


def service_url(mine: str) -> str:
    """`https://www.humanmine.org/humanmine` (or .../service) -> the properties endpoint."""
    mine = mine.rstrip("/")
    if not mine.endswith("/service"):
        mine += "/service"
    return mine + "/bluegenes-properties"


def key_for(path: Path) -> str:
    name = path.name
    for key, ext in EXTENSIONS.items():
        if name == key + ext:
            return key
    return name


def file_for(key: str) -> str:
    return key + EXTENSIONS.get(key, "")


# --- EDN layout formatting -------------------------------------------------
# BlueGenes stores a layout as one line of EDN. Only whitespace outside strings
# is changed here, which EDN ignores, so the pretty and compact forms are the
# same value; `compact` turns either back into BlueGenes' own spelling.

def _split_strings(text: str):
    """Yield (is_string, chunk), strings including their quotes and escapes."""
    i, n, start = 0, len(text), 0
    while i < n:
        if text[i] == '"':
            if start < i:
                yield False, text[start:i]
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            yield True, text[i:j + 1]
            i = start = j + 1
        else:
            i += 1
    if start < n:
        yield False, text[start:]


def compact(edn: str) -> str:
    # each run of whitespace outside strings becomes one space
    s = "".join(chunk if is_string else re.sub(r"\s+", " ", chunk)
                for is_string, chunk in _split_strings(edn))
    # no space just inside brackets, as BlueGenes writes it
    res, in_str, esc = [], False, False
    for i, ch in enumerate(s):
        if in_str:
            res.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        if ch == " " and ((res and res[-1] in "[{(") or (i + 1 < len(s) and s[i + 1] in "]})")):
            continue
        res.append(ch)
    return "".join(res).strip()


def pretty(edn: str) -> str:
    """Start every map that follows another value on a new line, indented by depth."""
    s = compact(edn)
    out, depth, in_str, esc = [], 0, False, False
    for i, ch in enumerate(s):
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{(":
            if ch == "{" and out and out[-1] in " [":
                if out[-1] == " ":
                    out.pop()
                out.append("\n" + "  " * depth)
            depth += 1
        elif ch in "]})":
            depth -= 1
        out.append(ch)
    return "".join(out).strip() + "\n"


def normalise(key: str, value: str) -> str:
    return compact(value) if key == "layout.report" else value.strip()


# --- HTTP ------------------------------------------------------------------

def request(method, url, token=None, body=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Token " + token
    data = None
    if body is not None:
        data = body.encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read().decode("utf-8", "replace")


def fetch(mine):
    status, text = request("GET", service_url(mine))
    if status != 200:
        sys.exit(f"GET {service_url(mine)} -> HTTP {status}: {text[:300]}")
    return json.loads(text)["bluegenes-properties"]


def write(mine, token, key, value, exists):
    """POST creates a key, PUT updates one (the service refuses the other way
    round). The PUT handler URL-decodes its body and then splits it on & and =,
    so a value containing either is sent as query parameters instead, where
    the servlet container parses it properly."""
    url = service_url(mine)
    form = urllib.parse.urlencode({"key": key, "value": value})
    if not exists:
        return request("POST", url, token, form)
    if "&" not in value and "=" not in value:
        return request("PUT", url, token, form)
    if len(url) + len(form) > MAX_QUERY_BYTES:
        sys.exit(f"{key}: the value contains '&' or '=' and is too long to send as query "
                 "parameters; edit it in the BlueGenes Admin page instead")
    return request("PUT", url + "?" + form, token, "")


# --- commands --------------------------------------------------------------

def cmd_export(args):
    props = fetch(args.mine)
    keys = args.key or sorted(props)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for key in keys:
        if key not in props:
            sys.exit(f"{key}: not set on {args.mine}; it has {sorted(props)}")
        value = props[key]
        text = pretty(value) if key == "layout.report" else value.rstrip("\n") + "\n"
        path = out / file_for(key)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path} ({key})")


def load(paths):
    return {key_for(Path(p)): Path(p).read_text(encoding="utf-8") for p in paths}


def differences(live, files):
    return [k for k, v in files.items() if normalise(k, live.get(k, "")) != normalise(k, v)]


def cmd_check(args):
    live = fetch(args.mine)
    files = load(args.files)
    diff = differences(live, files)
    for key in files:
        print(f"{key}: {'DIFFERS from' if key in diff else 'matches'} {args.mine}"
              + ("" if key in live else " (not set there)"))
    return 1 if diff else 0


def cmd_apply(args):
    token = args.token or os.environ.get("BLUEGENES_TOKEN")
    if args.token_file:
        token = Path(args.token_file).read_text().strip()
    if not token and not args.dry_run:
        sys.exit("A superuser API token is needed: --token-file FILE or BLUEGENES_TOKEN "
                 "(BlueGenes: log in as the superuser, Account, API access key)")
    live = fetch(args.mine)
    files = load(args.files)
    for key, value in files.items():
        value = normalise(key, value)
        if not value:
            sys.exit(f"{key}: the file is empty; refusing to store an empty value")
        if key == "layout.report" and not (value.startswith("{") and value.endswith("}")):
            sys.exit(f"{key}: does not look like an EDN map")
    changed = differences(live, files)
    if not changed:
        print("Nothing to do: the mine already has these values.")
        return 0
    if args.dry_run:
        print("Would write: " + ", ".join(changed))
        return 0
    backup = Path(args.backup_dir) / time.strftime("%Y%m%d-%H%M%S")
    backup.mkdir(parents=True, exist_ok=True)
    for key, value in live.items():
        (backup / file_for(key)).write_text(
            pretty(value) if key == "layout.report" else value + "\n", encoding="utf-8")
    print(f"Saved the current live values in {backup} (apply them from there to undo)")
    for key in changed:
        status, text = write(args.mine, token, key, normalise(key, files[key]), key in live)
        if status != 200:
            sys.exit(f"{key}: HTTP {status}: {text[:300]}")
        print(f"{key}: written")
    after = fetch(args.mine)
    still = differences(after, {k: files[k] for k in changed})
    if still:
        sys.exit(f"The mine reports different values after writing: {still}")
    print("Verified: the mine now has the values from the files. Reload a report page to see them.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("\n\n", 1)[1])
    sub = p.add_subparsers(dest="command", required=True)

    e = sub.add_parser("export", help="write the live properties to files")
    e.add_argument("mine", help="mine root, e.g. https://www.humanmine.org/humanmine")
    e.add_argument("-o", "--out", default=".", help="directory for the files (default: .)")
    e.add_argument("--key", action="append", help="only this property (repeatable)")
    e.set_defaults(func=cmd_export)

    c = sub.add_parser("check", help="compare files with the live mine")
    c.add_argument("mine")
    c.add_argument("files", nargs="+")
    c.set_defaults(func=cmd_check)

    a = sub.add_parser("apply", help="write files to the mine (superuser token)")
    a.add_argument("mine")
    a.add_argument("files", nargs="+")
    a.add_argument("--token", help="superuser API token (prefer --token-file or BLUEGENES_TOKEN)")
    a.add_argument("--token-file", help="file holding the token")
    a.add_argument("--backup-dir", default="bluegenes-backups",
                   help="where the current live values are saved first (default: bluegenes-backups)")
    a.add_argument("--dry-run", action="store_true", help="only say what would change")
    a.set_defaults(func=cmd_apply)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
