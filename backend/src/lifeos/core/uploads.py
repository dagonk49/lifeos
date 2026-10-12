"""Multipart uploads with a hard size limit (documents, dictations).

The body is counted while it streams in and the file stays in memory: an uploaded file's
plaintext never touches the disk before it is encrypted.
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect

from lifeos.core.errors import ProblemError

# Room for the form fields and multipart boundaries around the file itself.
FORM_OVERHEAD = 64 * 1024


@dataclass(frozen=True)
class Upload[F: BaseModel]:
    fields: F
    data: bytes
    filename: str | None


def _too_large(max_bytes: int) -> ProblemError:
    return ProblemError(
        413, "document_too_large", "The file is too large", extra={"max_bytes": max_bytes}
    )


def _invalid(loc: tuple[str, ...], msg: str, kind: str) -> RequestValidationError:
    return RequestValidationError([{"loc": ("body", *loc), "msg": msg, "type": kind}])


def _parse_fields[F: BaseModel](values: dict[str, str], model: type[F]) -> F:
    try:
        return model.model_validate(values)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]
        ) from exc


async def read_upload[F: BaseModel](request: Request, max_bytes: int, model: type[F]) -> Upload[F]:
    """Parses the multipart body with a hard size limit, counted while it streams in.

    The body is never read past the limit (nor at all if Content-Length announces more),
    and the file stays in memory: its plaintext never touches the disk.
    """
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise ProblemError(415, "unsupported_media_type", "Expected multipart/form-data")
    limit = max_bytes + FORM_OVERHEAD
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise _too_large(max_bytes)

    async def body() -> AsyncGenerator[bytes]:
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > limit:
                raise _too_large(max_bytes)
            yield chunk

    parser = MultiPartParser(
        request.headers, body(), max_files=1, max_fields=10, max_part_size=8 * 1024
    )
    parser.spool_max_size = limit
    try:
        form = await parser.parse()
    except MultiPartException as exc:
        raise ProblemError(400, "invalid_form", "Malformed multipart form") from exc
    except ClientDisconnect as exc:
        raise ProblemError(400, "upload_interrupted", "The upload was interrupted") from exc
    try:
        values: dict[str, str] = {}
        upload: UploadFile | None = None
        for key, value in form.multi_items():
            if isinstance(value, UploadFile):
                if key != "file":
                    raise _invalid((key,), "Unexpected file", "unexpected_file")
                upload = value
            elif key in values:
                raise _invalid((key,), "Field given more than once", "duplicate")
            elif value != "":  # an empty optional field means "not given"
                values[key] = value
        fields = _parse_fields(values, model)
        if upload is None:
            raise _invalid(("file",), "Field required", "missing")
        data = await upload.read()
        filename = upload.filename
    finally:
        await form.close()
    if len(data) > max_bytes:
        raise _too_large(max_bytes)
    if not data:
        raise ProblemError(422, "document_empty", "The file is empty")
    return Upload(fields=fields, data=data, filename=filename)
