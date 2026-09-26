"""Protocol-1 single-writer output and bounded shutdown; no application data."""
import json
import os
import queue
import threading

MAX_PACKET = 900 * 1024
MAX_INPUT = 1024 * 1024
_STOP = object()


def valid_seq(value):
    return type(value) is int and 0 <= value < 2**53


class Outbox:
    def __init__(self, stream, *, before_write=None):
        self.stream, self.before_write = stream, before_write
        self.lock = threading.Lock()
        self.queue = queue.Queue(maxsize=32)
        self.closed = False
        self.terminal_sent = threading.Event()
        self.writer = threading.Thread(target=self._write, name='outbox-writer', daemon=True)
        self.writer.start()

    @staticmethod
    def encode(packet):
        data = (json.dumps(packet, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
        if len(data) > MAX_PACKET:
            raise ValueError('packet_too_large')
        return data

    def put(self, packet):
        data = self.encode(packet)
        with self.lock:
            if self.closed:
                return False
            if packet.get('t') == 'msg' and packet.get('body', {}).get('op') == 'status':
                # Progress is a full snapshot. Preserve the latest queued one
                # so a fast local replay cannot lose its final completion state.
                with self.queue.mutex:
                    for index, item in enumerate(self.queue.queue):
                        if item[0].get('t') == 'msg' and item[0].get('body', {}).get('op') == 'status':
                            self.queue.queue[index] = (packet, data, False)
                            return True
            try:
                self.queue.put_nowait((packet, data, False))
                return True
            except queue.Full:
                if packet['t'] == 'msg':
                    return False
                raise RuntimeError('control_outbox_full')

    def close_with(self, terminal=None):
        data = self.encode(terminal) if terminal else None
        with self.lock:
            if self.closed:
                return
            self.closed = True
            controls = []
            while True:
                try:
                    item = self.queue.get_nowait()
                except queue.Empty:
                    break
                if item[0]['t'] != 'msg':
                    controls.append(item)
            for item in controls:
                self.queue.put_nowait(item)
            if terminal:
                self.queue.put_nowait((terminal, data, True))
            self.queue.put_nowait(_STOP)

    def _write(self):
        try:
            while True:
                item = self.queue.get()
                if item is _STOP:
                    self.terminal_sent.set()
                    return
                packet, data, terminal = item
                if self.before_write:
                    self.before_write(packet)
                self.stream.write(data)
                self.stream.flush()
                if terminal:
                    self.terminal_sent.set()
        except (OSError, ValueError):
            return


def shutdown(outbox, terminal=None, code=0):
    outbox.close_with(terminal)
    if not outbox.terminal_sent.wait(0.8):
        os._exit(code)
    return code
