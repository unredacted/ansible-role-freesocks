#!/usr/bin/env python3
"""Read a decoy web root for tests/test_decoy.yml.

Usage: decoy_site_check.py <web root> <node name>

Prints JSON: every file (relative), internal links that lead to no file the
root has, anything that would load from or link to another host, and which
parts of the node's name (split on '-') appear anywhere in the site.
"""
import json
import os
import re
import sys

root, node_name = sys.argv[1], sys.argv[2]
files, broken, external, text = [], [], [], ""
for d, _, names in os.walk(root):
    for f in names:
        p = os.path.join(d, f)
        rel = os.path.relpath(p, root)
        files.append(rel)
        with open(p, encoding="utf-8") as fh:
            body = fh.read()
        text += body.lower() + "\n"
        for u in re.findall(r'(?:href|src)="([^"]+)"', body):
            if u.startswith("/"):
                path = u.split("#")[0].split("?")[0]
                target = path.lstrip("/") + ("index.html" if path.endswith("/") else "")
                if not os.path.isfile(os.path.join(root, target or "index.html")):
                    broken.append(rel + " -> " + u)
            elif not u.startswith("#"):
                external.append(rel + " -> " + u)
        for u in re.findall(r'(?:https?:)?//[A-Za-z0-9.-]+\.[A-Za-z]{2,}[^\s"\')<>]*', body):
            if u != "http://www.w3.org/2000/svg":
                external.append(rel + " -> " + u)

print(json.dumps({
    "files": sorted(files),
    "broken": broken,
    "external": external,
    "name_parts": [w for w in node_name.lower().split("-") if re.search(r"\b" + re.escape(w) + r"\b", text)],
}))
