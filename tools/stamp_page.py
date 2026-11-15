#!/usr/bin/env python3
"""Point index.html at copies of the viewer's code whose NAMES carry a digest of their content:

  python3 tools/stamp_page.py

  css/style.css   -> css/style.<digest>.css
  js/*.js         -> js/v/<digest>/*.js      (copied together, so their relative imports keep working)

GitHub Pages' CDN ignores query strings when it caches, so `file?v=<digest>` is not enough: for up to ten
minutes after a deploy it can serve the previous file under the new URL, and the browser then keeps that stale
copy. A new file NAME can't be confused with an old one. Edit css/style.css and js/*.js as usual and run this
before publishing; it removes the copies of earlier builds. (Model files are versioned separately, by the build
id that tools/build_manifest.py writes into assets/manifest.json.)
"""
import glob, hashlib, os, re, shutil, sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def digest(paths):
    h = hashlib.sha1()
    for path in sorted(paths):
        h.update(os.path.basename(path).encode())
        h.update(open(path, "rb").read())
    return h.hexdigest()[:10]


css_src = os.path.join(root, "css", "style.css")
js_src = sorted(glob.glob(os.path.join(root, "js", "*.js")))
dc, dj = digest([css_src]), digest(js_src)

# this build's copies
css_out = os.path.join(root, "css", f"style.{dc}.css")
shutil.copyfile(css_src, css_out)
js_dir = os.path.join(root, "js", "v", dj)
os.makedirs(js_dir, exist_ok=True)
for f in js_src:
    shutil.copyfile(f, os.path.join(js_dir, os.path.basename(f)))
# earlier builds' copies
for old in glob.glob(os.path.join(root, "css", "style.*.css")):
    if old != css_out:
        os.remove(old)
for old in glob.glob(os.path.join(root, "js", "v", "*")):
    if os.path.abspath(old) != os.path.abspath(js_dir):
        shutil.rmtree(old)

page = os.path.join(root, "index.html")
html = open(page, encoding="utf-8").read()
new = re.sub(r'href="css/style(?:\.[0-9a-f]+)?\.css(?:\?v=[0-9a-f]+)?"', f'href="css/style.{dc}.css"', html)
new = re.sub(r'src="js/(?:v/[0-9a-f]+/)?main\.js(?:\?v=[0-9a-f]+)?"', f'src="js/v/{dj}/main.js"', new)
if f"css/style.{dc}.css" not in new or f"js/v/{dj}/main.js" not in new:
    sys.exit("index.html: could not find the stylesheet link or the module script to stamp")
if new != html:
    open(page, "w", encoding="utf-8").write(new)
print(f"index.html: css/style.{dc}.css, js/v/{dj}/main.js")
