"""Fixed-host HTTPS, bounded JSON, no proxies/redirects or upstream diagnostics."""
from dataclasses import dataclass
from decimal import Decimal
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 16 * 1024 * 1024
HOSTS = frozenset({'api.fugle.tw', 'www.twse.com.tw', 'api.finmindtrade.com'})
_DISABLED = set()
_AUTH_LOCK = threading.Lock()


class ClientError(Exception):
    def __init__(self, code, status=None):
        self.code, self.status = code, status
        super().__init__(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def secure_context():
    context = ssl.create_default_context()
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    for path in (os.environ.get('SSL_CERT_FILE'), '/etc/ssl/cert.pem',
                 '/etc/ssl/certs/ca-certificates.crt', '/etc/pki/tls/certs/ca-bundle.crt'):
        if context.cert_store_stats()['x509_ca']:
            break
        if path:
            try:
                context.load_verify_locations(cafile=path)
            except (OSError, ssl.SSLError):
                pass
    if not context.cert_store_stats()['x509_ca']:
        raise ClientError('tls_certificate_error')
    return context


def transport_error_code(exc):
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ssl.SSLCertVerificationError):
        return 'tls_certificate_error'
    if isinstance(reason, ssl.SSLError):
        return 'tls_error'
    return 'network_error'


def secure_opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect(),
        urllib.request.HTTPSHandler(context=secure_context()))


class RateLimiter:
    def __init__(self, interval=2.0, *, clock=time.monotonic, sleep=time.sleep):
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self._last = None
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            if self._last is not None:
                delay = self.interval - (self.clock() - self._last)
                if delay > 0:
                    self.sleep(delay)
            self._last = self.clock()


_LIMITERS = {host: RateLimiter(12.0 if host == 'api.finmindtrade.com' else 2.0) for host in HOSTS}


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError


@dataclass(frozen=True)
class Response:
    status: int
    payload: object


class JsonClient:
    def __init__(self, host, *, opener=None, limiter=None, sleep=time.sleep,
                 clock=time.monotonic):
        if host not in HOSTS:
            raise ClientError('host_not_allowed')
        self.host = host
        self._opener = opener  # Dependency injection for offline fake-server tests.
        self._limiter = limiter if limiter is not None else _LIMITERS[host]
        self._sleep, self._clock = sleep, clock

    def get(self, path, params, *, headers=None, allow_404=False):
        if not path.startswith('/') or path.startswith('//') or '?' in path or '#' in path:
            raise ClientError('invalid_path')
        url = 'https://' + self.host + path + '?' + urllib.parse.urlencode(params)
        for attempt in range(4):
            with _AUTH_LOCK:
                if self.host in _DISABLED:
                    raise ClientError('auth_disabled')
            self._limiter.wait()
            # Re-check after waiting: another caller may have received 401/403.
            with _AUTH_LOCK:
                if self.host in _DISABLED:
                    raise ClientError('auth_disabled')
            response = self._request(url, headers or {})
            if response.status in (401, 403):
                with _AUTH_LOCK:
                    _DISABLED.add(self.host)
                raise ClientError('auth_disabled', response.status)
            if response.status == 429 and attempt < 3:
                self._sleep((2, 4, 8)[attempt])
                continue
            if response.status == 404 and allow_404:
                return response
            if response.status != 200:
                raise ClientError('http_status', response.status)
            return response
        raise AssertionError('unreachable')

    def _request(self, url, headers):
        error = None
        try:
            opener = self._opener if self._opener is not None else secure_opener()
            request = urllib.request.Request(url, headers={
                'Accept': 'application/json', 'Accept-Encoding': 'identity',
                'User-Agent': 'trend-cast/0.1', **headers})
            started = self._clock()
            try:
                response = opener.open(request, timeout=20)
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()  # Never read or echo error bodies (may contain keys).
                return Response(status, None)
            with response:
                status = response.status
                if status != 200:
                    return Response(status, None)
                size = response.headers.get('Content-Length')
                if size is not None and (not size.isdigit() or int(size) > MAX_BYTES):
                    raise ClientError('response_too_large')
                if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    raise ClientError('unsupported_encoding')
                chunks, total = [], 0
                while True:
                    if self._clock() - started > 20:
                        raise ClientError('request_timeout')
                    chunk = response.read(min(65536, MAX_BYTES + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ClientError('response_too_large')
                    chunks.append(chunk)
                if self._clock() - started > 20:
                    raise ClientError('request_timeout')
                if size is not None and total != int(size):
                    raise ClientError('truncated_response')
                payload = json.loads(b''.join(chunks).decode('utf-8'), parse_float=Decimal,
                                     parse_constant=_invalid_constant, object_pairs_hook=_pairs)
                return Response(200, payload)
        except ClientError as exc:
            error = ClientError(exc.code, exc.status)
        except ssl.SSLError as exc:
            # SSLCertVerificationError also inherits ValueError.
            error = ClientError(transport_error_code(exc))
        except (UnicodeError, ValueError, RecursionError):
            error = ClientError('invalid_json')
        except Exception as exc:
            error = ClientError(transport_error_code(exc))
        # Outside except: no upstream exception/context retained on the safe error.
        raise error
