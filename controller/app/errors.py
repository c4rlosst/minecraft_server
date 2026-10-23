"""Plain words for the errors people actually hit, so the window can say what is wrong and what to do instead of showing a
raw message from rclone or Windows. summarize() never hides the original: callers also put the full text in the log."""
import re

LIMIT = ("free daily limit", "cap exceeded", "download_cap", "transaction_cap", "usage cap", "cap_exceeded")
KEYS = ("credential access key", "invalidaccesskeyid", "signaturedoesnotmatch", "access denied", "accessdenied",
        "unauthorized", "invalid_auth_token", "bad_auth_token", "statuscode: 403", "statuscode: 401", "forbidden",
        "access key id", "the aws access key")
BUCKET = ("nosuchbucket", "no such bucket", "bucket not found", "specified bucket does not exist")
NETWORK = ("no such host", "dial tcp", "i/o timeout", "timed out", "timeout", "connection refused", "connection reset",
           "network is unreachable", "tls handshake", "temporary failure in name resolution", "context deadline",
           "no route to host", "unexpected eof", "failed to open source object: no such host")


def _has(text: str, words) -> bool:
    return any(w in text for w in words)


def summarize(error) -> tuple:
    """(headline, detail) in plain words for an error or its message. The detail is one or two short sentences."""
    raw = str(error or "").strip()
    low = raw.lower()
    if _has(low, LIMIT):
        when = re.search(r"reset at ([0-9]{1,2}:[0-9]{2} ?[AP]M)", raw, re.I)
        return ("The free daily limit is used up",
                (f"It resets at {when.group(1)} your time. " if when else "It resets at midnight UTC. ") + "Your world is safe.")
    if _has(low, KEYS):
        return ("The storage refused the keys", "The key in the invite is wrong or was removed. Ask for a new invite.")
    if _has(low, BUCKET):
        return ("The bucket doesn't exist", "Check the invite: the bucket name may be wrong.")
    if _has(low, NETWORK):
        return ("Can't reach the internet", "Check the connection. A VPN or a firewall can block it. Try again in a minute.")
    if "rclone is not installed" in low:
        return ("A part of the app is missing", "Reinstall SMP Host.")
    first = re.split(r"(?<=[.!?])\s|\n| \| ", raw, maxsplit=1)[0] if raw else ""
    return ("Can't reach the shared world", first[:90] or "Something went wrong. Details are in the log.")


GENERIC = "Can't reach the shared world"


def headline(error) -> str:
    return summarize(error)[0]


def explain(error) -> str:
    """A plain sentence for an error we recognise, or '' when we don't (the caller then shows the original text)."""
    head, detail = summarize(error)
    return "" if head == GENERIC else f"{head}. {detail}"
