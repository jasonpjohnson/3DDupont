#!/usr/bin/env python3
"""Stamp index.html's script and stylesheet links with a digest of the viewer's code:

  python3 tools/stamp_page.py

  css/style.css?v=<digest>     js/main.js?v=<digest>

A browser may keep a file for as long as the host's cache headers allow (ten minutes on GitHub Pages) and would
otherwise run an old script against a new build. A changed file gets a new URL, so a plain reload is enough.
Run it after editing anything under js/ or css/ and before publishing. (Model files are versioned separately,
by the build id that tools/build_manifest.py writes into assets/manifest.json.)
"""
import hashlib, os, re, sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def digest(folder):
    h = hashlib.sha1()
    for name in sorted(os.listdir(os.path.join(root, folder))):
        path = os.path.join(root, folder, name)
        if os.path.isfile(path):
            h.update(name.encode())
            h.update(open(path, "rb").read())
    return h.hexdigest()[:10]


page = os.path.join(root, "index.html")
html = open(page, encoding="utf-8").read()
new = re.sub(r'(href="css/style\.css)(\?v=[0-9a-f]+)?(")', rf'\g<1>?v={digest("css")}\g<3>', html)
new = re.sub(r'(src="js/main\.js)(\?v=[0-9a-f]+)?(")', rf'\g<1>?v={digest("js")}\g<3>', new)
if 'css/style.css?v=' not in new or 'js/main.js?v=' not in new:
    sys.exit("index.html: could not find the stylesheet link or the module script to stamp")
if new != html:
    open(page, "w", encoding="utf-8").write(new)
print("index.html:", ", ".join(re.findall(r'(?:css/style\.css|js/main\.js)\?v=[0-9a-f]+', new)))
