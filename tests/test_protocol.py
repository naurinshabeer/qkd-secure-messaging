import socket
import threading
import unittest

from qkd_demo.protocol import (
    MAX_FRAME, TYPE_MESSAGE, Frame, ProtocolError, encode_frame, recv_frame,
)


class ProtocolTests(unittest.TestCase):
    def test_partial_reads_are_reassembled(self):
        left, right = socket.socketpair()
        expected = Frame(TYPE_MESSAGE, "QK-PARTIAL", b"n" * 12, b"ciphertext")
        wire = encode_frame(expected)
        result = []
        receiver = threading.Thread(target=lambda: result.append(recv_frame(right)))
        receiver.start()
        for byte in wire:
            left.sendall(bytes([byte]))
        receiver.join(timeout=2)
        left.close()
        right.close()
        self.assertFalse(receiver.is_alive())
        self.assertEqual(result, [expected])

    def test_truncated_frame_is_rejected(self):
        left, right = socket.socketpair()
        left.sendall(b"\x00\x00\x00\x40partial")
        left.close()
        with self.assertRaises(ProtocolError):
            recv_frame(right)
        right.close()

    def test_oversized_frame_is_rejected_before_body_read(self):
        left, right = socket.socketpair()
        left.sendall((MAX_FRAME + 1).to_bytes(4, "big"))
        with self.assertRaisesRegex(ProtocolError, "oversized"):
            recv_frame(right)
        left.close()
        right.close()

    def test_encoder_rejects_oversized_payload(self):
        with self.assertRaisesRegex(ProtocolError, "maximum"):
            encode_frame(Frame(TYPE_MESSAGE, "QK-X", bytes(12), bytes(16_385)))


if __name__ == "__main__":
    unittest.main()
