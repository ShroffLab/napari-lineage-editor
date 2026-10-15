"""Example private module — rename or delete once you have real code.

Modules are underscore-prefixed so the API docs are generated from the names
re-exported in ``__init__.py`` rather than from the file layout. See the
"Module layout" section of CONTRIBUTING.md.
"""


def greet(name: str) -> str:
    """Return a greeting for ``name``.

    Args:
        name: Who to greet.

    Returns:
        The greeting.
    """
    return f"Hello, {name}!"
