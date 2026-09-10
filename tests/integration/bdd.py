# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""The Given/When/Then vocabulary the integration scenarios are written in."""

import contextlib
import logging

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _step(keyword: str, text: str):
    """Log one scenario step, marking it if the step raises.

    Args:
        keyword: The Gherkin keyword, e.g. `GIVEN`.
        text: The step text.

    Raises:
        BaseException: Whatever the step raised, unchanged, so the traceback
            still points at the assertion that failed.
    """
    logger.info("%s %s", keyword.rjust(5), text)
    try:
        yield
    except BaseException:
        logger.error("%s %s [FAILED]", keyword.rjust(5), text)
        raise


def given(text: str):
    """State the world a scenario starts from.

    Args:
        text: The step text.

    Returns:
        A context manager wrapping the step.
    """
    return _step("GIVEN", text)


def when(text: str):
    """Perform the one action the scenario is about.

    Args:
        text: The step text.

    Returns:
        A context manager wrapping the step.
    """
    return _step("WHEN", text)


def then(text: str):
    """Assert what the action should have brought about.

    Args:
        text: The step text.

    Returns:
        A context manager wrapping the step.
    """
    return _step("THEN", text)


def and_(text: str):
    """Continue the preceding clause.

    Args:
        text: The step text.

    Returns:
        A context manager wrapping the step.
    """
    return _step("AND", text)
