"""Errors. Each one carries the exit code the CLI returns for it."""


class AnyxError(Exception):
    code = 1


class UsageError(AnyxError):
    code = 2


class SessionExpired(AnyxError):
    """X showed the login wall. Log the account in again (`anyx accounts add`)."""
    code = 3


class WrongAccount(AnyxError):
    """The browser opened as a different account than asked. Nothing was touched."""
    code = 4


class UIChanged(AnyxError):
    """A marker anyx relies on was not on the page. X probably changed its layout."""
    code = 5


class RateLimited(AnyxError):
    """X answered 429 or showed a rate-limit notice."""
    code = 6


class NotFound(AnyxError):
    """The post or account does not exist, or is not visible to this account."""
    code = 7


class ActionFailed(AnyxError):
    """The click went through but X did not confirm the change."""
    code = 8
