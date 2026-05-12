class InferenceError(RuntimeError):
    """Raised when the inference controller publishes an ERROR event."""


class InferenceTimeoutError(RuntimeError):
    """Raised when no terminal event arrives within the configured timeout."""
