"""
The HTTP/WebSocket half of the project: the FastAPI app, its configuration and
bind policy, the per-browser session registry, and the per-connection outbox.

This file exists so ``machinelearningmachine.server`` is a regular package like
its siblings (``agents``, ``protocol``, ``topologies``) rather than an implicit
PEP 420 namespace package. It shipped either way, but only by accident:
setuptools' ``packages.find`` defaults to ``namespaces = true``, and the moment
anyone sets that to ``false`` a directory without an ``__init__.py`` stops being
a package - the wheel would then build cleanly, install cleanly, and serve a 404
dashboard, because ``server/app.py`` and everything beside it were never copied.
"""
