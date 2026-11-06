#!/usr/bin/env python3
"""A fake `sysctl` for tests/test_front_sysctl.yml.

The kernel's values live in the JSON file $FAKE_SYSCTL_STATE; every `-p` is
appended to $FAKE_SYSCTL_STATE.applied. Supports what the role runs:
`sysctl -n KEY...` and `sysctl -q -p FILE`.
"""
import json
import os
import sys

state_path = os.environ["FAKE_SYSCTL_STATE"]
with open(state_path) as fh:
    state = json.load(fh)
args = sys.argv[1:]
if "-p" in args:
    path = args[args.index("-p") + 1]
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, v = (x.strip() for x in line.split("=", 1))
            if k not in state:
                sys.stderr.write("sysctl: cannot stat /proc/sys/%s\n" % k.replace(".", "/"))
                sys.exit(255)
            state[k] = " ".join(v.split())
    with open(state_path, "w") as fh:
        json.dump(state, fh)
    with open(state_path + ".applied", "a") as fh:
        fh.write(path + "\n")
    sys.exit(0)
if args and args[0] == "-n":
    for k in args[1:]:
        if k not in state:
            sys.stderr.write("sysctl: cannot stat /proc/sys/%s\n" % k.replace(".", "/"))
            sys.exit(255)
        print(state[k].replace(" ", "\t"))
    sys.exit(0)
sys.stderr.write("fake sysctl: unsupported %s\n" % args)
sys.exit(2)
