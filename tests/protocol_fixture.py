"""Subprocess-only gates. The production entry point has no test env branches."""
import argparse
import os
from pathlib import Path
import threading
import time

from back.jevcast import JevAnswer
from back.protocol import Outbox
from back.runtime import Application
from back.trendcast import main
from tests.helpers import KEY


class Client:
    def __init__(self, mode, directory):
        self.mode, self.directory = mode, directory
        self.lock = threading.Lock()
        self.count = 0

    def predict(self, point, **kwargs):
        with self.lock:
            self.count += 1
            number = self.count
            with (self.directory / 'calls').open('a') as stream:
                stream.write('request\n')
        if self.mode == 'blocked' or (self.mode == 'one_then_block' and number > 1):
            (self.directory / 'client-entered').touch()
            while not (self.directory / 'release-client').exists():
                time.sleep(.002)
        return JevAnswer('flat', {'up': .2, 'flat': .6, 'down': .2}, None)


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default='normal')
    parser.add_argument('--gates', type=Path, required=True)
    args, remaining = parser.parse_known_args()
    if args.mode in ('blocked', 'one_then_block', 'all_ok'):
        os.environ['TYPESAFE_API_KEY'] = KEY
    else:
        os.environ.pop('TYPESAFE_API_KEY', None)
    os.environ.pop('FUGLE_API_KEY', None)
    class FixtureOutbox(Outbox):
        def __init__(self, stream):
            def gate(packet):
                if args.mode == 'writer_gate' and packet['t'] == 'msg':
                    (args.gates / 'writer-entered').touch()
                    while not (args.gates / 'release-writer').exists():
                        time.sleep(.002)
            super().__init__(stream, before_write=gate)

        def close_with(self, terminal=None):
            super().close_with(terminal)
            (args.gates / 'outbox-closed').touch()
            if terminal:
                late = self.put({'t': 'msg', 'seq': terminal['seq'], 'body': {'op': 'late'}})
                (args.gates / ('late-accepted' if late else 'late-rejected')).touch()

    class FixtureApplication(Application):
        def __init__(self, *pos, **kw):
            super().__init__(*pos, jev_client=Client(args.mode, args.gates), **kw)

        def start(self):
            super().start()
            if args.mode == 'flood':
                for i in range(16):
                    self.emit({'op': 'fixture-flood', 'data': 'x' * (800 * 1024), 'index': i})
            (args.gates / 'up-handled').touch()

    if args.mode == 'oversized_day':
        import back.runtime
        back.runtime.day_view = lambda *a, **k: {'status': 'ok', 'data': '大' * 200000}
    return main(remaining, app_factory=FixtureApplication, outbox_factory=FixtureOutbox,
                startup_error='fixture_preflight_failure' if args.mode == 'preflight_fail' else None)


if __name__ == '__main__':
    raise SystemExit(run())
