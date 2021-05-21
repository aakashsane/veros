#!/usr/bin/env python

import os
import shutil
import datetime
import functools
import textwrap

import click
import entrypoints


SETUPDIR_ENVVAR = "VEROS_SETUP_DIR"
IGNORE_PATTERNS = ["__init__.py", "*.pyc", "__pycache__"]
SETUPS = {}

setup_dirs = []

for e in entrypoints.get_group_all("veros.setup_dirs"):
    try:
        setup_dirs.append(os.path.dirname(e.load().__file__))
    except ImportError:
        click.echo(f"Warning: Veros plugin {e!s} failed to import", err=True)

for setup_dir in os.environ.get(SETUPDIR_ENVVAR, "").split(";"):
    if os.path.isdir(setup_dir):
        setup_dirs.append(setup_dir)

# populate {setup_name: path} mapping
for setup_dir in setup_dirs:
    for setup in os.listdir(setup_dir):
        setup_path = os.path.join(setup_dir, setup)

        if not os.path.isdir(setup_path):
            continue

        if setup.startswith(("_", ".")):
            continue

        SETUPS[setup] = setup_path

SETUP_NAMES = sorted(SETUPS.keys())


def rewrite_main_file(target_file, setup_name):
    from veros import __version__ as veros_version

    current_date = datetime.datetime.utcnow()
    header_str = textwrap.dedent(
        f'''
        """
        This Veros setup file was generated by

           $ veros copy-setup {setup_name}

        on {current_date:%Y-%m-%d %H:%M:%S} UTC.
        """

        __VEROS_VERSION__ = {veros_version!r}

        if __name__ == "__main__":
            raise RuntimeError(
                "Veros setups cannot be executed directly. "
                f"Try `veros run {{__file__}}` instead."
            )

        # -- end of auto-generated header, original file below --
    '''
    ).strip()

    with open(target_file, "r") as f:
        orig_contents = f.readlines()

    shebang = None
    if orig_contents[0].startswith("#!"):
        shebang = orig_contents[0]
        orig_contents = orig_contents[1:]

    with open(target_file, "w") as f:
        if shebang is not None:
            f.write(shebang + "\n")

        f.write(header_str + "\n\n")
        f.writelines(orig_contents)


def copy_setup(setup, to=None):
    """Copy a standard setup to another directory.

    Available setups:

        {setups}

    Example:

        $ veros copy-setup global_4deg --to ~/veros-setups/4deg-lowfric

    Further directories containing setup templates can be added to this command
    via the {setup_envvar} environment variable.
    """
    if to is None:
        to = os.path.join(os.getcwd(), setup)

    if os.path.exists(to):
        raise RuntimeError("Target directory must not exist")

    to_parent = os.path.dirname(os.path.realpath(to))

    if not os.path.exists(to_parent):
        os.makedirs(to_parent)

    ignore = shutil.ignore_patterns(*IGNORE_PATTERNS)
    shutil.copytree(SETUPS[setup], to, ignore=ignore)

    main_setup_file = os.path.join(to, f"{setup}.py")
    rewrite_main_file(main_setup_file, setup)


copy_setup.__doc__ = copy_setup.__doc__.format(setups=", ".join(SETUP_NAMES), setup_envvar=SETUPDIR_ENVVAR)


@click.command("veros-copy-setup")
@click.argument("setup", type=click.Choice(SETUP_NAMES), metavar="SETUP")
@click.option(
    "--to",
    required=False,
    default=None,
    type=click.Path(dir_okay=False, file_okay=False, writable=True),
    help=("Target directory, must not exist " "(default: copy to current working directory)"),
)
@functools.wraps(copy_setup)
def cli(*args, **kwargs):
    copy_setup(*args, **kwargs)
