"""node_qdisc_plan: what to run so the interfaces that are up take a qdisc.

net.core.default_qdisc only reaches a qdisc when it is created, so an
interface that is already up keeps the one it has. tasks/setup/sysctl.yml
reads each interface with `tc -j qdisc show dev DEV` and passes the registered
loop results here:

  results | node_qdisc_plan(want, replaceable, mine=False)
      -> {'run': [{'dev': DEV, 'argv': [...]}], 'kept': ['DEV (why)']}

An interface is touched only when every leaf qdisc on it (the root, or the
children of a multiqueue `mq` root) is `want` or one of `replaceable`. Listed
in `kept` and left alone: one that carries anything else (somebody's
shaping), one whose root somebody attached by hand (a handle that is not 0:,
so it may carry options and filters; unless `mine`, which says the role
attached it), and one tc could not read. `argv` is what `tc` is given:

  a root the kernel attached by default (handle 0:)
      mq root   qdisc replace dev DEV root mq    (new children take the default)
      otherwise qdisc replace dev DEV root WANT
  a root the role attached (`mine`)
      qdisc del dev DEV root     (the kernel attaches its default again)

Both forms rely on net.core.default_qdisc already being `want`.
"""
import json

_SKIP = ("ingress", "clsact")


def _read(text):
    try:
        shown = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(shown, list):
        return None
    shown = [q for q in shown if isinstance(q, dict) and q.get("kind") not in _SKIP]
    roots = [q for q in shown if q.get("root")]
    if len(roots) != 1:
        return None
    root = roots[0]
    if root.get("kind") == "mq":
        leaves = [q.get("kind") for q in shown if not q.get("root")]
    else:
        leaves = [root.get("kind")]
    if not leaves:
        return None
    return root, set(leaves)


def node_qdisc_plan(results, want, replaceable, mine=False):
    allowed = set(replaceable) | {want}
    run, kept = [], []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        dev = str(r.get("item"))
        read = _read(r.get("stdout")) if r.get("rc") == 0 else None
        if read is None:
            kept.append("%s (not read)" % dev)
            continue
        root, leaves = read
        if leaves == {want}:
            continue
        if not leaves <= allowed:
            if leaves != {"noqueue"}:
                kept.append("%s (%s)" % (dev, ", ".join(sorted(leaves))))
            continue
        attached = str(root.get("handle", "0:")).split(":")[0] not in ("", "0")
        if attached and not mine:
            kept.append("%s (%s, set by hand)" % (dev, ", ".join(sorted(leaves))))
            continue
        if attached:
            argv = ["qdisc", "del", "dev", dev, "root"]
        elif root.get("kind") == "mq":
            argv = ["qdisc", "replace", "dev", dev, "root", "mq"]
        else:
            argv = ["qdisc", "replace", "dev", dev, "root", want]
        run.append({"dev": dev, "argv": argv})
    return {"run": run, "kept": kept}


class FilterModule(object):
    def filters(self):
        return {"node_qdisc_plan": node_qdisc_plan}
