"""Example drop-in integration plugin.

Place .py files in <config-root>/plugins/ and they are auto-loaded at
startup. Use this file as a template for new transports or form adapters.
"""
from browserstonks.integrations import BaseIntegration

ENTRYPOINTS = {
    "browserstonks_integrations": {
        "example": BaseIntegration,  # replace with your subclass
    },
}
