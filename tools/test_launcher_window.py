"""Exercise the shipped WPF controls and player trust path against isolated local test realms."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import itertools
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
from threading import Thread
from unittest.mock import patch


def trust_cases(root, temp, address, certificate):
    from player_connection import connect
    from player_tls_channels import entered_address
    for value,want in (("example.org","https://example.org:18443"),("https://example.org","https://example.org:18443"),
                       ("localhost:443","https://localhost"),("::1","https://[::1]:18443"),("[::1]:1324","https://[::1]:1324")):
        assert entered_address(value)==want
    for bad in ("http://localhost","https://x:0","x:65536","https://user@x","host/path","https://x?",""):
        try:entered_address(bad)
        except ValueError:pass
        else:raise AssertionError(bad)
    scratch=temp/"empty trust profile";scratch.mkdir()
    with patch.dict(os.environ,LOCALAPPDATA=str(scratch)):
        result=connect(scratch,{"address":address})
        fingerprint=hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate.read_text("ascii"))).hexdigest()
        assert result["code"]=="fingerprint_required" and result["fingerprint"]==fingerprint and not (scratch/"player-settings.json").exists()
        result=connect(scratch,{"address":address,"fingerprint":"0"*64})
        assert result["code"]=="trust_changed" and not (scratch/"player-settings.json").exists()
        result=connect(scratch,{"address":address,"fingerprint":fingerprint})
        assert result["ok"] and Path(result["caFile"]).name=="of-leaf-"+fingerprint+".pem"
        assert connect(scratch,{"address":address})["ok"]
        try:connect(scratch,{"address":address.replace("localhost","127.0.0.1"),"fingerprint":fingerprint})
        except ssl.SSLCertVerificationError:pass
        else:raise AssertionError("wrong hostname accepted")
    print("PASS TLS default/explicit ports, IPv6, unsigned first contact, declined or changed fingerprint, verified pin reuse and hostname rejection",flush=True)


def compile_probe(root):
    framework=Path(os.environ["WINDIR"])/"Microsoft.NET/Framework64/v4.0.30319"
    wpf=framework/"WPF"
    binary=root/"launcher-window-qa.exe"
    subprocess.run([str(framework/"csc.exe"),"/nologo","/platform:x64","/target:exe","/out:"+str(binary),
                    "/r:System.Windows.Forms.dll","/r:System.Xaml.dll",
                    *("/r:"+str(wpf/(name+".dll")) for name in ("WindowsBase","PresentationCore","PresentationFramework")),
                    str(Path(__file__).with_suffix(".cs"))],check=True)
    return binary


def exercise_window(root,temp,cert,key):
    from local_identity import LocalIdentity
    from realm_accounts import AccountService, AccountError
    import player_captcha
    identity=LocalIdentity(temp/"wpf-accounts.sqlite")
    accounts=AccountService(identity)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def handle(self):
            try:
                preface=self.rfile.readline(64)
            except ConnectionResetError:
                return  # A certificate-only probe may close without opening an account channel.
            if preface==b"OF-SF/1 ACCOUNT\n":
                super().handle()
        def reply(self,status,data):
            blob=json.dumps(data).encode()
            self.send_response(status);self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(blob)));self.end_headers();self.wfile.write(blob)
        def do_GET(self):
            self.reply(200,{"realm":"overfield-local-v1","account_features":["captcha_lookup_v1","password_change_v1"]})
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            try:
                result=self.server.accounts.request(self.path,body,"127.0.0.1","127.0.0.1",True,True,gateway_source="window-fixture")
                if self.path.rsplit("/",1)[-1] in ("login","register"):
                    result.update(import_ticket="ofi_"+"a"*43,import_expires_in=300)
                self.reply(200,result)
            except AccountError as error:self.reply(error.status,{"error":error.code})
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
    with ThreadingHTTPServer(("127.0.0.1",0),Handler) as http, ThreadingHTTPServer(("127.0.0.1",0),Handler) as second_http:
        http.accounts=accounts
        second_http.accounts=AccountService(LocalIdentity(temp/"wpf-other-realm.sqlite"))
        second_http.socket=context.wrap_socket(second_http.socket,server_side=True)
        other_work=Thread(target=second_http.serve_forever,daemon=True);other_work.start()
        alternate="https://localhost:"+str(second_http.server_port)
        http.socket=context.wrap_socket(http.socket,server_side=True)
        work=Thread(target=http.serve_forever,daemon=True);work.start()
        url="https://localhost:"+str(http.server_port)
        prefs=root/"player-settings.json"
        original=prefs.read_bytes() if prefs.is_file() else None
        prefs.write_text(json.dumps({"source":str(temp/"Original"),"base":"","publicTls":url,"caFile":str(cert),
                         "servers":[{"publicTls":url,"caFile":str(cert)},{"publicTls":alternate,"caFile":str(cert)}]}),encoding="utf8")
        try:
            rotation=temp/"rotation-profile";rotation.mkdir()
            with patch.dict(os.environ, LOCALAPPDATA=str(rotation)):
                from player_connection import connect
                leaf=connect(rotation,{"address":url})
                assert connect(rotation,{"address":url,"fingerprint":leaf["fingerprint"]})["ok"]
                second,key2=temp/"rotation.pem",temp/"rotation.key"
                subprocess.run(["openssl","req","-config",str(temp/"openssl-test.cnf"),"-newkey","rsa:2048","-nodes","-x509","-days","1","-subj","/CN=localhost",
                                "-addext","subjectAltName=DNS:localhost","-addext","basicConstraints=critical,CA:TRUE","-addext","keyUsage=critical,digitalSignature,keyCertSign",
                                "-keyout",str(key2),"-out",str(second)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                context.load_cert_chain(second,key2)
                assert connect(rotation,{"address":url})["code"]=="trust_changed"
                new=connect(rotation,{"address":url,"renew":True})
                assert new["code"]=="fingerprint_required" and new["fingerprint"]!=leaf["fingerprint"]
                assert connect(rotation,{"address":url,"renew":True,"fingerprint":new["fingerprint"]})["ok"]
                context.load_cert_chain(cert,key)
            binary=compile_probe(root)
            # Only this in-process test issuer uses deterministic challenge text. The binary and player CLI are unmodified.
            answers=itertools.cycle("23456")
            with patch.object(player_captcha.secrets,"choice",side_effect=lambda choices:next(answers)):
                subprocess.run([str(binary)],check=True,timeout=145,env={**os.environ,"OF_TEST_OTHER_REALM":alternate})
            import private_account_api
            transport={"publicTls":url,"caFile":str(cert)}
            def verify_password(password):
                return private_account_api.handle({"action":"login","server":transport,"username":"本地测试OwO","password":password})
            assert verify_password("window-test-password")["code"]=="invalid_credentials"
            assert verify_password("\U0001f680"*128)["ok"]
        finally:
            if original is None:prefs.unlink(missing_ok=True)
            else:prefs.write_bytes(original)
            http.shutdown();work.join(5);second_http.shutdown();other_work.join(5)
