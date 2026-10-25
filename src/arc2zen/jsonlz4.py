"""Mozilla JSONLZ4 block files using liblz4, without a Python dependency."""

import ctypes
import ctypes.util
import json
from pathlib import Path

from .common import MigrationError, require

_lib = None
MAX_SIZE = 100_000_000


def library():
    global _lib
    if _lib is None:
        names = [
            ctypes.util.find_library("lz4"),
            "/opt/homebrew/lib/liblz4.dylib",
            "/usr/local/lib/liblz4.dylib",
            "liblz4.so.1",
        ]
        for name in filter(None, names):
            try:
                _lib = ctypes.CDLL(name)
                break
            except OSError:
                continue
        if _lib is None:
            raise MigrationError("liblz4 is missing; install it with: brew install lz4")
        _lib.LZ4_decompress_safe.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
        ]
        _lib.LZ4_compress_default.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
        ]
        _lib.LZ4_compressBound.argtypes = [ctypes.c_int]
    return _lib


def decode(data):
    require(len(data) >= 13 and data[:8] == b"mozLz40\0", "Unsupported JSONLZ4 header")
    length = int.from_bytes(data[8:12], "little")
    require(0 < length < MAX_SIZE, "JSONLZ4 payload exceeds the supported size")
    buf = ctypes.create_string_buffer(length)
    size = library().LZ4_decompress_safe(data[12:], buf, len(data) - 12, length)
    require(size == length, "JSONLZ4 decompression failed")
    return json.loads(buf.raw)


def encode(value):
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    require(0 < len(data) < MAX_SIZE, "JSONLZ4 payload exceeds the supported size")
    capacity = library().LZ4_compressBound(len(data))
    buf = ctypes.create_string_buffer(capacity)
    size = library().LZ4_compress_default(data, buf, len(data), capacity)
    require(size > 0, "JSONLZ4 compression failed")
    result = b"mozLz40\0" + len(data).to_bytes(4, "little") + buf.raw[:size]
    require(decode(result) == value, "JSONLZ4 round-trip failed")
    return result


def read(path):
    return decode(Path(path).read_bytes())


def write(path, value):
    Path(path).write_bytes(encode(value))
