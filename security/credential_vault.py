"""Credentials are NEVER stored by AI JobPilot.

There is deliberately no function here that accepts, stores or returns a password, token or cookie value. Logging in is always done by
the candidate themselves in a visible browser window (see browser/session_manager.manual_login). The only thing that may be kept is an
*encrypted* browser session (cookies) when PERSIST_BROWSER_SESSIONS=true and SECRET_KEY is set - see browser/session_manager.py.
"""

STORES_PASSWORDS = False


def refuse(*_args, **_kwargs):
    raise PermissionError("Credentials are not stored or entered by this tool. Log in yourself in the browser window.")


store_password = get_password = fill_password = refuse
