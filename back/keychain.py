"""macOS scheduling fallback. Never persist credentials or report child output."""
from contextlib import contextmanager
import os
import subprocess
import sys

SERVICES = {'FUGLE_API_KEY': 'trendcast-fugle', 'TYPESAFE_API_KEY': 'trendcast-typesafe'}


def read_password(service, *, executable='/usr/bin/security', platform=None):
    if (sys.platform if platform is None else platform) != 'darwin':return None
    account = os.environ.get('USER')
    if not account:return None
    try:
        result = subprocess.run([executable, 'find-generic-password', '-s', service,
                                 '-a', account, '-w'], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5)
        if result.returncode != 0:return None
        value = result.stdout.decode('utf-8').removesuffix('\n')
        if not value or len(value) > 4096 or any(c.isspace() or ord(c) < 32 for c in value):return None
        return value
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return None


@contextmanager
def credentials(*, lookup=None):
    """Keep existing environment values; remove temporary fallbacks on every exit."""
    lookup = lookup or read_password
    previous = {}
    try:
        for name, service in SERVICES.items():
            if os.environ.get(name):continue
            value = lookup(service)
            if value:
                previous[name] = os.environ.get(name)
                os.environ[name] = value
        yield [name for name in SERVICES if not os.environ.get(name)]
    finally:
        for name, old in previous.items():
            if old is None:os.environ.pop(name, None)
            else:os.environ[name] = old
