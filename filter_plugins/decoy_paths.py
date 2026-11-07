"""decoy_dirs: the directories a list of relative file paths needs.

`['index.html', 'a/b/index.html'] | decoy_dirs` -> ['a', 'a/b'], used by
tasks/setup/decoy.yml to create a kit's directories before rendering into
them and to keep them when it removes what the site does not have.
"""


def decoy_dirs(paths):
    dirs = set()
    for p in paths:
        parts = str(p).split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    return sorted(dirs)


class FilterModule(object):
    def filters(self):
        return {"decoy_dirs": decoy_dirs}
