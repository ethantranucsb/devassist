"""Run the CLI as ``python -m devassist``.

A fallback for systems whose security policy blocks the ``devassist``
launcher, such as Windows machines that block newly created executables.
"""

from devassist.cli import app

if __name__ == "__main__":
    app(prog_name="devassist")
