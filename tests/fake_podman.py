#!/usr/bin/env python3
"""A recording stand-in for the podman executable.

Selected through ``CAULDRON_PODMAN_PATH`` it behaves like a real subprocess:
each invocation appends its argv as a JSON line to ``$FAKE_PODMAN_LOG`` and the
exit code and output are driven by ``$FAKE_PODMAN_RESPONSES``.

The responses file is a JSON list of rules. The first rule whose ``argv`` is a
prefix of the invocation's argv is used; a rule without ``argv`` matches every
command. Rules may set ``stdout``, ``stderr`` and ``returncode``. With no
matching rule the command succeeds with empty output.
"""

import json
import os
import sys


def _load_responses():
    path = os.environ.get("FAKE_PODMAN_RESPONSES")
    if not path:
        return []
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []


def _matches(rule, argv):
    prefix = rule.get("argv")
    if prefix is None:
        return True
    return argv[: len(prefix)] == list(prefix)


def main():
    argv = sys.argv[1:]

    log = os.environ.get("FAKE_PODMAN_LOG")
    if log:
        try:
            with open(log, "a") as handle:
                handle.write(json.dumps({"argv": argv}) + "\n")
        except OSError:
            pass

    for rule in _load_responses():
        if _matches(rule, argv):
            sys.stdout.write(rule.get("stdout", ""))
            sys.stderr.write(rule.get("stderr", ""))
            return rule.get("returncode", 0)

    return 0


if __name__ == "__main__":
    sys.exit(main())
