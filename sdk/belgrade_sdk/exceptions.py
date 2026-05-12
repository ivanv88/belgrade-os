class InferenceError(RuntimeError):
    """Raised when the inference controller publishes an ERROR event."""


class InferenceTimeoutError(InferenceError):
    """Raised when no terminal event arrives within the configured timeout."""
