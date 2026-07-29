from .ews_client import translate_ews_error

_CANDIDATES_LIMIT = 20


def resolve_person(account, query: str) -> dict:
    """Resolve a partial name or address against the Exchange address book.

    Wraps EWS ResolveNames. "No results" is a normal outcome (empty
    candidates list), not an error - EWS itself returns it as a caught,
    non-fatal ErrorNameResolutionNoResults.
    """
    try:
        mailboxes = account.protocol.resolve_names([query])
    except Exception as exc:
        raise translate_ews_error(exc) from exc

    candidates = [
        {"name": mailbox.name, "email": mailbox.email_address}
        for mailbox in mailboxes
        if not isinstance(mailbox, Exception) and mailbox.email_address
    ]
    return {"candidates": candidates[:_CANDIDATES_LIMIT]}
