"""Builder-agnostic generation of the final project image's build context.

Both the podman and smolvm backends use this to lay down the same project
image (``cauldron`` user matching the host UID/GID, ``openssh-server``,
injected lifecycle scripts, optional entrypoint) before handing the context to
whichever builder is available. Keeping the generation in one place guarantees
the two backends build byte-identical images.
"""

import pathlib

from cauldron import config

CONTAINER_HOME = "/home/cauldron"


def materialize_project_build(context, uid, gid, base, scripts, project_dir):
    """Write the project-image Dockerfile and lifecycle scripts into ``context``.

    ``context`` is a directory that will be the build context. Returns the
    build-args dict to pass to the builder: ``CAULDRON_BASE`` always, plus
    ``CAULDRON_RUN_POST_BUILD`` when a ``post_build`` script is present.
    """
    context = pathlib.Path(context)
    scripts = scripts or {}
    project_dir = pathlib.Path(project_dir or ".").resolve()

    lines = [
        "ARG CAULDRON_BASE",
        "FROM ${CAULDRON_BASE}",
        "USER root",
        f"RUN groupadd -g {gid} -o cauldron && useradd -m -u {uid} -g {gid} -o cauldron && usermod -p '*' cauldron",
        "RUN apt-get update && apt-get install -y --no-install-recommends openssh-server && rm -rf /var/lib/apt/lists/*",
        f"RUN mkdir -p /run/sshd /home/cauldron/.ssh /home/cauldron/.config && chown {uid}:{gid} /home/cauldron/.ssh /home/cauldron/.config && chmod 700 /home/cauldron/.ssh",
        f"ENV HOME={CONTAINER_HOME}",
    ]

    hooks = []
    for hook, value in scripts.items():
        content = config.resolve_script(value, project_dir).read_text()
        (context / f"{hook}.sh").write_text(content)
        hooks.append(hook)
        lines.append(f"COPY {hook}.sh /usr/local/share/cauldron/{hook}.sh")
        lines.append(f"RUN chmod +x /usr/local/share/cauldron/{hook}.sh")

    lines.append("USER cauldron")

    build_args = {"CAULDRON_BASE": base}
    if "post_build" in hooks:
        lines.extend(
            [
                "ARG CAULDRON_RUN_POST_BUILD=false",
                'RUN if [ "$CAULDRON_RUN_POST_BUILD" = "true" ]; then /usr/local/share/cauldron/post_build.sh; fi',
            ]
        )
        build_args["CAULDRON_RUN_POST_BUILD"] = "true"
    if "entrypoint" in hooks:
        lines.append('ENTRYPOINT ["/usr/local/share/cauldron/entrypoint.sh"]')

    (context / "Dockerfile").write_text("\n".join(lines) + "\n")
    return build_args
