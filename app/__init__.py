"""Cymbal Superstores Operations Agent package.

Exposes ``app`` (the ADK :class:`App` with telemetry wired in) as the primary entry
point, which is what ADK's agent loader, ``agents-cli`` and the Agent Runtime
container all resolve. ``root_agent`` / ``cymbal_operations_agent`` remain available
for callers that construct their own ``Runner``.
"""

from app.agent import app, cymbal_operations_agent, root_agent

__all__ = ["app", "cymbal_operations_agent", "root_agent"]
