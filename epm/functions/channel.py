"""Message framing between the engine and the function worker."""

from __future__ import annotations

import pickle
import struct


class Channel:
    """Length-prefixed pickle frames over a pair of binary streams."""

    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer

    def send(self, obj) -> None:
        data = pickle.dumps(obj, protocol=5)
        self.writer.write(struct.pack("<Q", len(data)) + data)
        self.writer.flush()

    def recv(self):
        header = self.reader.read(8)
        if len(header) < 8:
            raise EOFError
        (size,) = struct.unpack("<Q", header)
        data = self.reader.read(size)
        if len(data) < size:
            raise EOFError
        return pickle.loads(data)
