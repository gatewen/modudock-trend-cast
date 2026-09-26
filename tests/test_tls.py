import io
import json
import os
import ssl
import traceback
import unittest
from unittest.mock import Mock, patch
import urllib.error

from back import http_client
from back.fugle import FugleClient
from back.http_client import ClientError, secure_context
from back.jevcast import JevClient
from back.twse import FIELDS, TwseClient
from tests.helpers import KEY


class EmptyCA:
    def __init__(self, works=None, initial=0):
        self.works, self.count = works, initial
        self.paths = []
        self.verify_mode = ssl.CERT_NONE
        self.check_hostname = False

    def cert_store_stats(self):
        return {'x509_ca': self.count}

    def load_verify_locations(self, *, cafile):
        self.paths.append(cafile)
        if cafile == self.works:
            self.count = 1
        else:
            raise OSError(KEY)


class Reply(io.BytesIO):
    status = 200
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())
        self.headers = {}


class TLSTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'FUGLE_API_KEY': KEY}, clear=True)
        env.start(); self.addCleanup(env.stop)
        http_client._DISABLED.clear()
        self.addCleanup(http_client._DISABLED.clear)
        limiter = Mock()
        self.requests = [
            lambda: FugleClient(limiter=limiter).candles('2330', '2024-09-01', '2024-09-30'),
            lambda: TwseClient(limiter=limiter).events('2330', '2024-09-01', '2024-09-30'),
            lambda: JevClient()._request(b'{}', KEY)]

    @staticmethod
    def reply(request, timeout):
        if 'api.fugle.tw' in request.full_url:
            return Reply({'symbol': '2330', 'timeframe': '1', 'data': []})
        if 'www.twse.com.tw' in request.full_url:
            return Reply(dict(stat='OK', strDate='20240901', endDate='20240930', fields=FIELDS, data=[]))
        return Reply({})

    def test_empty_default_ca_without_env_all_three_clients_use_verified_fallback(self):
        self.assertNotIn('SSL_CERT_FILE', os.environ)
        for fetch in self.requests:
            with self.subTest(client=fetch):
                context = EmptyCA('/etc/ssl/cert.pem')
                opener = Mock(); opener.open.side_effect = self.reply
                with patch('back.http_client.ssl.create_default_context', return_value=context), \
                     patch('back.http_client.urllib.request.build_opener', return_value=opener) as build:
                    fetch()
                self.assertEqual(context.paths, ['/etc/ssl/cert.pem'])
                self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                self.assertIs(context.check_hostname, True)
                self.assertIs(build.call_args.args[-1]._context, context)
                opener.open.assert_called_once()

    def test_ordered_fallback_env_then_system_paths_and_existing_ca_untouched(self):
        paths = ['/custom/ca.pem', '/etc/ssl/cert.pem',
                 '/etc/ssl/certs/ca-certificates.crt', '/etc/pki/tls/certs/ca-bundle.crt']
        with patch.dict(os.environ, {'SSL_CERT_FILE': paths[0]}):
            for index, path in enumerate(paths):
                with self.subTest(path=path):
                    context = EmptyCA(path)
                    with patch('back.http_client.ssl.create_default_context', return_value=context):
                        self.assertIs(secure_context(), context)
                    self.assertEqual(context.paths, paths[:index+1])
            context = EmptyCA(initial=1)
            with patch('back.http_client.ssl.create_default_context', return_value=context):
                secure_context()
            self.assertEqual(context.paths, [])

    def test_no_ca_refuses_all_three_clients_before_constructing_opener(self):
        for fetch in self.requests:
            with patch('back.http_client.ssl.create_default_context', return_value=EmptyCA()), \
                 patch('back.http_client.urllib.request.build_opener') as build:
                with self.assertRaisesRegex(ClientError, '^tls_certificate_error$') as caught:
                    fetch()
                build.assert_not_called()
                self.assertIsNone(caught.exception.__context__)
                self.assertNotIn(KEY, ''.join(traceback.format_exception(caught.exception)))

    def test_tls_and_network_reasons_are_classified_without_exception_context(self):
        for original, code in ((ssl.SSLCertVerificationError(KEY), 'tls_certificate_error'),
                (urllib.error.URLError(ssl.SSLCertVerificationError(KEY)), 'tls_certificate_error'),
                (ssl.SSLError(KEY), 'tls_error'), (urllib.error.URLError(OSError(KEY)), 'network_error')):
            for fetch in self.requests:
                with self.subTest(code=code), \
                     patch('back.http_client.ssl.create_default_context', return_value=EmptyCA(initial=1)), \
                     patch('back.http_client.urllib.request.build_opener') as build:
                    build.return_value.open.side_effect = original
                    with self.assertRaisesRegex(ClientError, '^' + code + '$') as caught:
                        fetch()
                    self.assertIsNone(caught.exception.__context__)
                    self.assertNotIn(KEY, ''.join(traceback.format_exception(caught.exception)))
