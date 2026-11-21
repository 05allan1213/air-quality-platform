from io import BytesIO

from django.conf import settings
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser


class BoundedJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        limit = settings.DATA_UPLOAD_MAX_MEMORY_SIZE
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ParseError(code="invalid_request")
        try:
            return super().parse(BytesIO(raw), media_type, parser_context)
        except (RecursionError, UnicodeError) as exc:
            raise ParseError(code="invalid_request") from exc
