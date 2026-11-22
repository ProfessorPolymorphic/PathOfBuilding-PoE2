"""Path of Building share codes <-> build XML.

A share code is the build XML, zlib-deflated, then base64 with the URL-safe
alphabet ('-' and '_' instead of '+' and '/'). PoB's headless runtime stubs out
Inflate/Deflate, so we do this in Python and hand the engine plain XML.
"""

from __future__ import annotations

import base64
import re
import zlib


class InvalidBuildCode(ValueError):
    pass


def decode(code: str) -> str:
    """Share code -> XML. Accepts a bare code; whitespace and padding are forgiven."""
    code = re.sub(r"\s+", "", code)
    code += "=" * (-len(code) % 4)
    try:
        data = base64.urlsafe_b64decode(code)
        xml = zlib.decompress(data).decode("utf-8")
    except Exception as e:  # binascii.Error, zlib.error, UnicodeDecodeError
        raise InvalidBuildCode("not a valid Path of Building code") from e
    if "<PathOfBuilding" not in xml[:500]:
        raise InvalidBuildCode("decoded data is not a Path of Building build")
    return xml


def encode(xml: str) -> str:
    """XML -> share code, in the same format PoB generates."""
    return base64.urlsafe_b64encode(zlib.compress(xml.encode("utf-8"), 9)).decode("ascii")


def looks_like_xml(text: str) -> bool:
    return text.lstrip().startswith("<?xml") or text.lstrip().startswith("<PathOfBuilding")
