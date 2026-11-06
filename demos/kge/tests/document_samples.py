"""Generated documents for the reader contracts; every declared extension needs a sample here."""
from __future__ import annotations

import base64
import struct
import zlib

TEXT = "**Definition.** Takings are what the shop sold in a period."
TABLE = "| Period | Takings |\n| --- | --- |\n| June | 42 |"
CHART = "Chart: Takings rise from 20 in May to 42 in June."


def png() -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + bytes([35, 90, 160]) * 32) * 32)) + chunk(b"IEND", b""))


def pdf(scan=False) -> bytes:
    """Two-page PDF: a text layer, a table and a chart, or raster-only pages with no text layer."""
    if scan:
        drawing = b"q 100 0 0 100 50 600 cm /Im0 Do Q"
    else:
        drawing = (b"BT /F1 12 Tf 50 750 Td (Takings are what the shop sold in a period.) Tj "
                   b"0 -30 Td (Period    Takings) Tj 0 -20 Td (June      42) Tj ET "
                   b"50 600 20 20 re f 90 600 20 42 re f")
    pixels = zlib.compress((bytes([35, 90, 160]) * 32) * 32)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
               *[b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
                 b"<< /Font << /F1 5 0 R >> /XObject << /Im0 7 0 R >> >> /Contents 6 0 R >>"] * 2,
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(drawing)).encode() + b" >>\nstream\n" + drawing + b"\nendstream",
               b"<< /Type /XObject /Subtype /Image /Width 32 /Height 32 /ColorSpace /DeviceRGB /BitsPerComponent 8 "
               b"/Filter /FlateDecode /Length " + str(len(pixels)).encode() + b" >>\nstream\n" + pixels + b"\nendstream"]
    output, offsets = b"%PDF-1.4\n", [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(output)
    output += f"xref\n0 {len(offsets)}\n{0:010} 65535 f \n".encode()
    output += b"".join(f"{n:010} 00000 n \n".encode() for n in offsets[1:])
    return output + f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()


def sample(format: str) -> bytes:
    if format in {"md", "txt"}:
        return TEXT.encode()
    if format == "csv":
        return b"Period,Takings\nJune,42\n"
    if format == "tsv":
        return b"Period\tTakings\nJune\t42\n"
    if format in {"html", "htm"}:
        return (f"<html><body><p>{TEXT}</p><table><tr><th>Period</th><th>Takings</th></tr>"
                f"<tr><td>June</td><td>42</td></tr></table><img alt='{CHART}'></body></html>").encode()
    if format == "pdf":
        return pdf()
    if format == "png":
        return png()
    if format == "webp":
        return base64.b64decode("UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA")
    if format in {"heic", "heif"}:
        content = base64.b64decode(ENCODED["heic"])
        return content if format == "heic" else content[:8] + b"mif1" + content[12:]
    if format in {"jpg", "jpeg"}:
        return base64.b64decode(ENCODED["jpeg" if format == "jpg" else format])
    raise AssertionError(f"no generated sample for declared format {format}")


ENCODED = {'heic': 'AAAAJGZ0eXBoZWljAAAAAG1pZjFNaVBybWlhZk1pSEJoZWljAAABhm1ldGEAAAAAAAAAIWhkbHIAAAAAAAAAAHBpY3QAAAAAAAAAAAAAAAAAAAAAJGRpbmYAAAAcZHJlZgAAAAAAAAABAAAADHVybCAAAAABAAAADnBpdG0AAAAAAAEAAAAjaWluZgAAAAAAAQAAABVpbmZlAgAAAAABAABodmMxAAAAAOZpcHJwAAAAxWlwY28AAAATY29scm5jbHgAAgACAAaAAAAADGNsbGkAywBAAAAAFGlzcGUAAAAAAAAAIAAAACAAAAAJaXJvdAAAAAAQcGl4aQAAAAADCAgIAAAAcWh2Y0MBA3AAAACwAAAAAAAe8AD8/fj4AAALA6AAAQAXQAEMAf//A3AAAAMAsAAAAwAAAwAecCShAAEAI0IBAQNwAAADALAAAAMAAAMAHqAUIEHAgwjiHuRZVNwICBgCogABAAlEAcBhcshAUyQAAAAZaXBtYQAAAAAAAAABAAEGgQIDBYaEAAAAHmlsb2MAAAAARAAAAQABAAAAAQAAAboAAAA9AAAAAW1kYXQAAAAAAAAATQAAADkoAa+i9kbFf/71S73wFH/7BmY6//6Nsr/9T6v/1mj3TLJn+iEQv/8e1j/tiRf3egRdhX4K2sZNkVw=', 'jpeg': '/9j/4AAQSkZJRgABAQAASABIAAD/4QBMRXhpZgAATU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAIKADAAQAAAABAAAAIAAAAAD/7QA4UGhvdG9zaG9wIDMuMAA4QklNBAQAAAAAAAA4QklNBCUAAAAAABDUHYzZjwCyBOmACZjs+EJ+/8AAEQgAIAAgAwEiAAIRAQMRAf/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQEAAABfQECAwAEEQUSITFBBhNRYQcicRQygZGhCCNCscEVUtHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeIiYqSk5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn6Onq8fLz9PX29/j5+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAABAgMEBQYHCAkKC//EALURAAIBAgQEAwQHBQQEAAECdwABAgMRBAUhMQYSQVEHYXETIjKBCBRCkaGxwQkjM1LwFWJy0QoWJDThJfEXGBkaJicoKSo1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdoaWpzdHV2d3h5eoKDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uLj5OXm5+jp6vLz9PX29/j5+v/bAEMAAgICAgICAwICAwUDAwMFBgUFBQUGCAYGBgYGCAoICAgICAgKCgoKCgoKCgwMDAwMDA4ODg4ODw8PDw8PDw8PD//bAEMBAgICBAQEBwQEBxALCQsQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEP/dAAQAAv/aAAwDAQACEQMRAD8A+R6KKK/rA/nsKKKKAP/Q+R6KKK/rA/nsKKKKAP/Z'}
