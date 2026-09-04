"""Entrypoint for `python -m a2a_mcp`.

Equivalent to invoking the `a2a-mcp` console script; exists so the
project supports both invocation styles.
"""

from __future__ import annotations

from .server import main

if __name__ == "__main__":
    main()
