from __future__ import annotations


class ProviderExecutionError(RuntimeError):
    """A provider failure carrying secret-free routing evidence."""

    def __init__(self, message: str, *, routing_json: str = "{}") -> None:
        super().__init__(message)
        self.routing_json = routing_json


class TransientProviderError(ProviderExecutionError):
    """An external capacity or network failure that is safe to retry."""


class NotificationDeliveryRejectedError(RuntimeError):
    """The notification provider confirmed that delivery was rejected."""


class NotificationDeliveryUncertainError(RuntimeError):
    """The notification request may have reached the provider."""


class SpendGuardrailReached(ProviderExecutionError):
    """The TransPodcast-specific Modal Environment budget is exhausted."""


class DiscoveryAlreadyRunningError(RuntimeError):
    """A durable discovery lease is already held by another trigger."""


class TranslationStalledError(RuntimeError):
    """A durable Translation Project stopped making target progress."""

    def __init__(
        self,
        message: str,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class TranslationProviderError(RuntimeError):
    """A Translation Project provider turn failed after reporting usage.

    ``kind`` carries the classified Agent failure (``auth``,
    ``rate_limit``, ``provider``, or ``schema``) when the runner reported
    one, so the Processing Service can choose the Retry Policy.
    """

    def __init__(
        self,
        message: str,
        *,
        input_tokens: int,
        output_tokens: int,
        kind: str | None = None,
    ) -> None:
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.kind = kind


class TranslationProviderMismatchError(RuntimeError):
    """A Translation Project belongs to a different Agent provider."""
