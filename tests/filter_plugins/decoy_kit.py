"""A TEST-ONLY decoy kit filter for tests/test_decoy.yml. Not a decoy.

It exists to exercise the role's kit contract (tasks/setup/decoy.yml): a
kit's `decoy_kit` filter turns the machine's reference into
{site: {...}, pages: [{template, dest, vars}]}. A real kit lives in private
inventory; never point remnawave_decoy_kit at tests/decoy-kit.

The reference `deadbeef` returns a page that escapes the web root, so the
test can check that the role refuses it.
"""
import random


def decoy_kit(ref):
    rng = random.Random("test-kit:" + str(ref))
    items = ["%s%04d" % (rng.choice("abcdef"), rng.randrange(10000)) for _ in range(rng.randint(2, 4))]
    site = {"title": "Test kit %s" % rng.randrange(1000), "prefix": rng.choice(["item", "entry", "page"])}
    pages = [{"template": "index.html.j2", "dest": "index.html", "vars": {"entries": items}},
             {"template": "404.html.j2", "dest": "404.html"}]
    pages += [{"template": "item.html.j2", "dest": "%s/%s/index.html" % (site["prefix"], i), "vars": {"id": i}} for i in items]
    if ref == "deadbeef":
        pages.append({"template": "404.html.j2", "dest": "../escape.html"})
    return {"site": site, "pages": pages}


class FilterModule(object):
    def filters(self):
        return {"decoy_kit": decoy_kit}
