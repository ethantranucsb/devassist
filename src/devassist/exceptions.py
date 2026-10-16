"""Errors that devassist raises on purpose.

The CLI catches DevassistError, prints its message and exits with a nonzero
code, so every error a user can cause or fix should subclass it.
"""


class DevassistError(Exception):
    """Base class for every error devassist raises on purpose."""


class ConfigError(DevassistError):
    """A setting is missing or invalid."""


class MissingAPIKeyError(ConfigError):
    """No Anthropic API key is configured."""
