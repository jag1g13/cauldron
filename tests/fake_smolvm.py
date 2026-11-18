#!/usr/bin/env python3
"""A recording stand-in for the smolvm executable.

Selected through ``CAULDRON_SMOLVM_PATH`` it behaves like a real subprocess:
each invocation appends its argv as a JSON line to ``$FAKE_SMOLVM_LOG`` and the
exit code and output are driven by ``$FAKE_SMOLVM_RESPONSES``.

The responses file is a JSON list of rules. The first rule whose ``argv`` is a
prefix of the invocation's argv is used; a rule without ``argv`` matches every
command. Rules may set ``stdout``, ``stderr``, ``returncode`` and ``times``. A
rule with ``times`` matches at most that many times, tracked in
``$FAKE_SMOLVM_STATE`` so later rules can take over; with no matching rule the
command succeeds with empty output.
"""

import json
import os
import pathlib
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


def _write_output(argv):
    """Create the file named after ``-o`` to model a successful export."""
    for index, arg in enumerate(argv):
        if arg == "-o" and index + 1 < len(argv):
            path = pathlib.Path(argv[index + 1])
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fake-image-archive")
            except OSError:
                pass
            return


def main():
    argv = sys.argv[1:]

    log = os.environ.get("FAKE_SMOLVM_LOG")
    if log:
        try:
            with open(log, "a") as handle:
                handle.write(json.dumps({"argv": argv}) + "\n")
        except OSError:
            pass

    rules = _load_json(os.environ.get("FAKE_SMOLVM_RESPONSES"), [])
    state_path = os.environ.get("FAKE_SMOLVM_STATE")
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

        returncode = rule.get("returncode", 0)
        if returncode == 0:
            _write_output(argv)
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        return returncode

    _write_output(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
