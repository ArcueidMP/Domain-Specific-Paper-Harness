"""Domain-specific exceptions."""


class DomainInvariantError(ValueError):
    """Raised when trusted application data violates a domain invariant."""


class DuplicateDailyRunError(RuntimeError):
    """Raised when another logical daily run already exists or holds its lock."""
