#!/usr/local/bin/python3
"""Protocol-1 entry point. The stdin control thread never waits for DB/network."""
import argparse
import json
import os
from pathlib import Path
import sys
from xml.parsers import expat

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from back.db_writer import DBWriter
from back.protocol import MAX_INPUT, Outbox, shutdown, valid_seq
from back.runtime import Application
from back.store import DEFAULT_DB
from back.news_digest import decode_packet


def preflight(python_version=None, expat_version=None):
    if (python_version or sys.version_info[:3]) < (3, 12):
        return 'Python >= 3.12 is required'
    if (expat_version or expat.version_info) < (2, 6):
        return 'Expat >= 2.6 is required'
    return None


def main(argv=None, *, app_factory=Application, writer_factory=DBWriter, outbox_factory=Outbox,
         stdin=None, stdout=None, startup_error=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    stdin = stdin if stdin is not None else sys.stdin.buffer
    stdout = stdout if stdout is not None else sys.stdout.buffer
    error = startup_error or preflight()
    outbox = outbox_factory(stdout)
    writer = writer_factory(args.db) if error is None else None
    app = None
    seq = None
    ready = False
    running = False

    def finish():
        if app is not None:
            app.close()
        elif writer is not None:
            writer.close()
        return shutdown(outbox, {'t': 'done', 'seq': seq} if seq is not None else None)

    def initialized(future):
        nonlocal ready
        if outbox.closed:
            return
        if future.exception() is not None:
            shutdown(outbox, {'t': 'fail', 'seq': seq, 'reason': 'database_open_failed'}, 1)
            os._exit(1)
        else:
            ready = True
            outbox.put({'t': 'ready', 'seq': seq})

    while True:
        line = stdin.readline(MAX_INPUT + 1)
        if not line:
            return finish()
        if len(line) > MAX_INPUT:
            print('discard: oversized input', file=sys.stderr, flush=True)
            return finish()
        try:
            packet, body_size = decode_packet(line)
        except (ValueError, UnicodeError, RecursionError):
            print('discard: invalid JSON', file=sys.stderr, flush=True)
            continue
        if not isinstance(packet, dict) or not valid_seq(packet.get('seq')):
            print('discard: invalid seq', file=sys.stderr, flush=True)
            continue
        kind = packet.get('t')
        if seq is None:
            if kind != 'hello':
                print('discard: expected hello', file=sys.stderr, flush=True)
                continue
            seq = packet['seq']
            if error:
                return shutdown(outbox, {'t': 'fail', 'seq': seq, 'reason': error}, 1)
            writer.ready.add_done_callback(initialized)
        elif packet['seq'] != seq:
            print('discard: seq mismatch', file=sys.stderr, flush=True)
        elif kind == 'bye':
            return finish()
        elif kind == 'up' and ready and not running:
            running = True
            app = app_factory(writer, outbox, seq)
            app.start()
        elif kind == 'msg' and running:
            app.request(packet.get('body'))
        elif kind == 'event' and running:
            app.event(packet.get('topic'), packet.get('body'), wire_size=body_size)
        else:
            print('discard: unexpected message', file=sys.stderr, flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
