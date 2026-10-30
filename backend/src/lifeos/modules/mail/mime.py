"""The exact message LifeOS sends (task 18): plain text, RFC 5322, deterministic.

Built with the standard library (`email.message.EmailMessage`) from the approved
parameters only. Headers come from validated values (no line break can reach them:
`header_safe`), the body is plain text (never HTML), attachments are base64. The
Message-ID is derived from the approval (`operations.idempotency_key`): the same approval
always yields the same Message-ID, which is what the reconciliation looks for at the
provider before anything is sent again (ADR 0018).

The boundary of a multipart message is derived from the same key, so that a message is
byte-for-byte reproducible from its parameters and its date (golden tests).
"""

import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime

# 7bit: every non-ASCII header is RFC 2047 encoded, every body quoted-printable or base64;
# CRLF line endings, as on the wire.
POLICY = policy.SMTP.clone(cte_type="7bit")

_UNSAFE = re.compile("[\\x00-\\x1f\\x7f\\u0085\\u2028\\u2029]")
_FILENAME_UNSAFE = re.compile(r'[\x00-\x1f\x7f"\\/:*?<>|]')


def header_safe(value: str) -> bool:
    """No control character (CR, LF, NUL...) that could end a header or add one."""
    return not _UNSAFE.search(value)


@dataclass(frozen=True)
class Attachment:
    filename: str
    mime: str
    data: bytes


@dataclass(frozen=True)
class Reply:
    # The Message-ID of the message answered ("<...>"), when known.
    message_id_header: str | None


def message_id(key: str, from_address: str) -> str:
    """`<key@domain of the sender>`: unique per approval, no server name revealed."""
    domain = from_address.rsplit("@", 1)[-1].lower()
    return f"<{key}@{domain}>"


def filename_for(title: str, mime: str) -> str:
    """An attachment's file name from a document's title and type, on one line."""
    name = " ".join(_FILENAME_UNSAFE.sub(" ", title).split())[:150] or "piece-jointe"
    extension = mimetypes.guess_extension(mime, strict=False) or ""
    if extension == ".jpe":
        extension = ".jpg"
    if extension and not name.lower().endswith(extension):
        name += extension
    return name


def build(
    *,
    key: str,
    from_address: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body: str,
    attachments: list[Attachment],
    reply: Reply | None,
    date: datetime,
) -> bytes:
    """The raw message. Raises ValueError on a value that cannot be a header."""
    for value in (from_address, subject, *to, *cc, *bcc):
        if not header_safe(value):
            raise ValueError("unsafe header value")
    message = EmailMessage(policy=POLICY)
    message["From"] = from_address
    if to:
        message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    if bcc:
        # Kept in the raw message: Gmail reads the recipients from the headers, delivers to
        # these addresses and strips this header from the delivered copies.
        message["Bcc"] = ", ".join(bcc)
    message["Subject"] = subject
    message["Date"] = format_datetime(date)
    message["Message-ID"] = message_id(key, from_address)
    if reply is not None and reply.message_id_header and header_safe(reply.message_id_header):
        message["In-Reply-To"] = reply.message_id_header
        message["References"] = reply.message_id_header
    message["MIME-Version"] = "1.0"
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    message.set_content(text, subtype="plain", charset="utf-8")
    for attachment in attachments:
        maintype, _, subtype = attachment.mime.partition("/")
        if not maintype or not subtype:
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(
            attachment.data,
            maintype=maintype,
            subtype=subtype,
            filename=attachment.filename,
            cte="base64",
        )
    if attachments:
        message.set_boundary(f"=_{key}")
    return message.as_bytes(policy=POLICY)
