"""Entry point for ``python -m vox``.

Installed as the ``vox`` module run target.
Delegates to :func:`vox.cli.main`.
"""

from vox.cli import main

main()
