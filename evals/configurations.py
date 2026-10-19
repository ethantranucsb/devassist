"""The three configurations the eval compares (docs/decisions.md D9 and D18).

Model names come from ``devassist.config``, so the eval always measures the
models the tool ships with. Only the design gets the second review, so Haiku
alone shows what the review adds.
"""

from dataclasses import dataclass

from devassist.config import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_MODEL,
    DEFAULT_REVIEW_MODEL,
    ESCALATION_ROUND,
    MAX_ROUNDS,
    MAX_SNIPPET_CHARS,
    Settings,
)

DESIGN = "design"  # the shipped design, judged against D8's bar
BASELINE = "sonnet"  # the configuration the bar compares the design with


@dataclass(frozen=True)
class Configuration:
    """One way to run devassist.

    Attributes:
        name: What the results call it.
        model: The model for the rounds before the escalation round.
        fallback_model: The model from the escalation round on.
        review: Whether the review model checks a passing answer for missed issues.
    """

    name: str
    model: str
    fallback_model: str
    review: bool = False


CONFIGURATIONS: dict[str, Configuration] = {
    configuration.name: configuration
    for configuration in (
        Configuration(DESIGN, DEFAULT_MODEL, DEFAULT_FALLBACK_MODEL, review=True),
        Configuration("haiku", DEFAULT_MODEL, DEFAULT_MODEL),
        Configuration(BASELINE, DEFAULT_FALLBACK_MODEL, DEFAULT_FALLBACK_MODEL),
    )
}


def settings_for(configuration: Configuration, base: Settings) -> Settings:
    """Apply a configuration's models and review, with the loop at its shipped values.

    The round cap, escalation round, size limit and review model are reset
    to their defaults, so a value in the developer's ``.env`` can't change
    what the eval measures.

    Args:
        configuration: The models and review to use.
        base: The loaded settings, which supply the API key.

    Returns:
        A copy of the settings for this configuration.
    """
    return base.model_copy(
        update={
            "model": configuration.model,
            "fallback_model": configuration.fallback_model,
            "review": configuration.review,
            "review_model": DEFAULT_REVIEW_MODEL,
            "max_rounds": MAX_ROUNDS,
            "escalation_round": ESCALATION_ROUND,
            "max_snippet_chars": MAX_SNIPPET_CHARS,
        }
    )
