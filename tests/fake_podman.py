#!/usr/bin/env python3
"""A recording stand-in for the podman executable.

Selected through ``CAULDRON_PODMAN_PATH`` it behaves like a real subprocess:
each invocation appends its argv as a JSON line to ``$FAKE_PODMAN_LOG`` and the
exit code and output are driven by ``$FAKE_PODMAN_RESPONSES``.

The responses file is a JSON list of rules. The first rule whose ``argv`` is a
prefix of the invocation's argv is used; a rule without ``argv`` matches every
command. Rules may set ``stdout``, ``stderr``, ``returncode`` and ``times``. A
rule with ``times`` matches at most that many times, tracked in
``$FAKE_PODMAN_STATE`` so later rules can take over; with no matching rule the
command succeeds with empty output.
"""

import json
import os
import sys


def _load_json(path, default):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


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

    rules = _load_json(os.environ.get("FAKE_PODMAN_RESPONSES"), [])
    state_path = os.environ.get("FAKE_PODMAN_STATE")
    state = _load_json(state_path, {}) if state_path else {}

    for index, rule in enumerate(rules):
        if not _matches(rule, argv):
            continue
        times = rule.get("times")
        if times is not None:
            key = str(index)
            used = state.get(key, 0)
            if used >= times:
                continue
            state[key] = used + 1
            if state_path:
                try:
                    with open(state_path, "w") as handle:
                        json.dump(state, handle)
                except OSError:
                    pass

        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        return rule.get("returncode", 0)

    return 0


if __name__ == "__main__":
    sys.exit(main())
