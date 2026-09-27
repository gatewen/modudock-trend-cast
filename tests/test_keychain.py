from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from back import keychain
from back.store import Store
from scripts.daily_forward import run_once, main

SECRET='fake-keychain-secret-never-log'


class KeychainTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.security=self.root/'security'
        env=patch.dict(os.environ,{'USER':'fixture-user'},clear=True);env.start();self.addCleanup(env.stop)

    def fake(self,code):
        self.security.write_text('#!'+sys.executable+'\nimport sys,time\n'+code+'\n')
        self.security.chmod(0o700)

    def lookup(self,service):return keychain.read_password(service,executable=str(self.security),platform='darwin')

    def test_fake_security_exact_argv_success_env_priority_and_cleanup(self):
        self.fake("assert sys.argv[1:] == ['find-generic-password','-s','trendcast-typesafe','-a','fixture-user','-w']\nprint("+repr(SECRET)+")")
        os.environ['FUGLE_API_KEY']='environment-wins'
        seen=[]
        def lookup(service):seen.append(service);return self.lookup(service)
        with keychain.credentials(lookup=lookup) as missing:
            self.assertEqual(missing,[]);self.assertEqual(os.environ['FUGLE_API_KEY'],'environment-wins')
            self.assertEqual(os.environ['TYPESAFE_API_KEY'],SECRET)
        self.assertNotIn('TYPESAFE_API_KEY',os.environ)
        self.assertEqual(os.environ['FUGLE_API_KEY'],'environment-wins')
        self.assertEqual(seen,['trendcast-typesafe'])

    def test_both_services_and_empty_env_restored_even_on_exception(self):
        self.fake("assert sys.argv[3] in ('trendcast-fugle','trendcast-typesafe')\nprint(sys.argv[3]+'-secret')")
        os.environ['FUGLE_API_KEY']=''
        with self.assertRaises(RuntimeError):
            with keychain.credentials(lookup=self.lookup):
                self.assertEqual(os.environ['FUGLE_API_KEY'],'trendcast-fugle-secret')
                self.assertEqual(os.environ['TYPESAFE_API_KEY'],'trendcast-typesafe-secret')
                raise RuntimeError('fixture')
        self.assertEqual(os.environ['FUGLE_API_KEY'],'');self.assertNotIn('TYPESAFE_API_KEY',os.environ)

    def test_default_absolute_path_nonmac_and_absent_account_skip(self):
        with patch('back.keychain.sys.platform','linux'),patch('back.keychain.subprocess.run',side_effect=AssertionError('no subprocess')):
            self.assertIsNone(keychain.read_password('trendcast-fugle'))
        with patch('back.keychain.sys.platform','darwin'),patch('back.keychain.subprocess.run',return_value=subprocess.CompletedProcess([],0,b'key\n')) as run:
            self.assertEqual(keychain.read_password('trendcast-fugle'),'key')
            self.assertEqual(run.call_args.args[0][0],'/usr/bin/security')
            self.assertEqual(run.call_args.kwargs['timeout'],5)
            self.assertIs(run.call_args.kwargs['stderr'],subprocess.DEVNULL)
            self.assertIs(run.call_args.kwargs['stdin'],subprocess.DEVNULL)
        os.environ.pop('USER')
        with patch('back.keychain.subprocess.run',side_effect=AssertionError('missing account')):
            self.assertIsNone(self.lookup('trendcast-fugle'))

    def test_missing_executable_failed_exit_empty_and_malformed_password_skip(self):
        self.assertIsNone(self.lookup('trendcast-fugle'))
        for code in ("print("+repr(SECRET)+");sys.exit(1)","print('')", "print('two\\nlines')", "sys.stdout.buffer.write(b'\\xff')", "print('x'*4097)"):
            with self.subTest(code=code):
                self.fake(code);self.assertIsNone(self.lookup('trendcast-fugle'))

    def test_real_five_second_timeout_does_not_leak_partial_output(self):
        self.fake("print("+repr(SECRET)+",flush=True);print("+repr(SECRET)+",file=sys.stderr,flush=True);time.sleep(6)")
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):self.assertIsNone(self.lookup('trendcast-fugle'))
        self.assertEqual(out.getvalue()+err.getvalue(),'')

    def test_cli_failure_safe_and_no_child_stderr_in_process_logs(self):
        self.fake("print("+repr(SECRET)+");print("+repr(SECRET)+",file=sys.stderr);sys.exit(1)")
        # Capture OS-level stderr, not just Python's sys.stderr.
        code="from back.keychain import credentials,read_password\nfrom scripts.daily_forward import main\nfrom unittest.mock import patch\nlookup=lambda service:read_password(service,executable="+repr(str(self.security))+",platform='darwin')\nwith patch('scripts.daily_forward.credentials',lambda:credentials(lookup=lookup)): main(['--db',"+repr(str(self.root/'missing'))+"])"
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,check=True)
        self.assertEqual(result.stderr,'');self.assertNotIn(SECRET,result.stdout)
        value=json.loads(result.stdout);self.assertEqual(value['status'],'missing_keys');self.assertEqual(value['jev_http_calls'],0)

    def test_cli_uses_fallback_in_memory_then_restores_on_service_error(self):
        self.fake('print('+repr(SECRET)+')')
        path=self.root/'db.sqlite3'
        with Store(path):pass
        def fail(writer,gate,**kwargs):
            self.assertEqual(os.environ['FUGLE_API_KEY'],SECRET);self.assertEqual(os.environ['TYPESAFE_API_KEY'],SECRET)
            raise RuntimeError(SECRET)
        out,err=io.StringIO(),io.StringIO()
        with patch('scripts.daily_forward.credentials',lambda:keychain.credentials(lookup=self.lookup)),redirect_stdout(out),redirect_stderr(err):
            rc=main(['--db',str(path)],stdout=out,runner=lambda p:run_once(p,ledger_path=self.root/'ledger',service_factory=fail))
        self.assertEqual(rc,1);self.assertNotIn(SECRET,out.getvalue()+err.getvalue())
        self.assertNotIn('FUGLE_API_KEY',os.environ);self.assertNotIn('TYPESAFE_API_KEY',os.environ)
        self.assertEqual(json.loads(out.getvalue())['error'],'daily_forward_failed')
