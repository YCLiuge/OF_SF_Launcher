"""Verify the player ZIP in a new profile using loopback TLS, synthetic accounts and local fixtures."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import zipfile


def three_ports():
    with ExitStack() as stack:
        sockets=[stack.enter_context(socket.socket()) for _ in range(3)]
        for sock in sockets: sock.bind(("127.0.0.1",0))
        return [s.getsockname()[1] for s in sockets]


def wait_ready(get, child):
    for _ in range(180):
        try:
            if get():
                return
        except (ConnectionError,TimeoutError):
            pass
        if child.poll() is not None:
            raise AssertionError("isolated service exited: "+str(child.returncode))
        time.sleep(.1)
    raise TimeoutError("isolated test service")


def inside(root, temp):
    server=root/"OverField-Server"
    sys.path[:0]=[str(root/"client_tools"),str(server/"app"),str(server/"app/upstream")]
    from native_launch import json_call, is_running
    import player_patch as patch
    import private_account_api as accounts
    from player_tls_channels import player_address, PlayerTlsChannels
    from local_game_wire import encode, receive
    from local_identity import LocalIdentity
    from network.msg_id import MsgId
    from proto import net_pb2 as pb
    from remote_password import RemotePassword
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from test_player_save_import import privacy,over_tls
    archive=privacy(accounts,temp/"backup-fixture")

    print("Checking packaged UI and separate client staging",flush=True)
    if os.name=="nt":
        assert subprocess.run([str(root/patch.EXE),"--check-ui"],check=False,timeout=12).returncode==0
        assert subprocess.run([str(root/patch.EXE),"--realm-picker","--check-ui"],check=False,timeout=12).returncode==0
        assert subprocess.run([str(root/patch.EXE),"--reconnect","--check-ui"],check=False,timeout=12).returncode==0
    for bad in ("http://127.0.0.1:18443","https://user@host","https://host/x","https://host:0","https://host?x=1"):
        try:player_address(bad)
        except ValueError:pass
        else:raise AssertionError("invalid public address accepted")
    assert player_address("https://Example.Org:443/")=="https://example.org"
    source=temp/"Original"
    source.mkdir()
    (source/"launcher.exe").write_bytes(b"official-fixture")
    fixture=source/"static.bundle"
    fixture.write_bytes(os.urandom(200))
    original=fixture.read_bytes()
    base=temp/"Selected storage"
    base.mkdir()
    home=patch.choose_home(root,source,str(base))
    patch.stage(root,home)
    assert (home/patch.EXE).is_file() and (home/"OverField-Server/runtime-win64/python.exe").is_file()
    patch.own_work(home,True)
    client=home/"Client";client.mkdir()
    os.link(fixture,client/"static.bundle")
    log=client/"private-channel.log";log.write_bytes(b"");log.chmod(0o400)
    stamp={"instance":"1"*32,"source":str(source)}
    patch.save(client/".of-private-client.json",stamp)
    patch.save(home/"client-config.json",stamp)
    unrelated=home/"personal.txt";unrelated.write_text("keep")
    patch.uninstall(home)
    assert not client.exists() and unrelated.read_text()=="keep" and fixture.read_bytes()==original and not is_running(home)
    # A fresh ZIP extracted over unrelated files must not claim or remove them.
    (home/"State").mkdir()
    try:patch.uninstall(home)
    except ValueError:pass
    else:raise AssertionError("accepted unrelated workspace")
    assert (home/"State").is_dir()

    cert,key=temp/"test-ca.pem",temp/"test-key.pem"
    ssl_tool=shutil.which("openssl")
    if not ssl_tool: raise RuntimeError("test fixture requires OpenSSL")
    settings=temp/"openssl-test.cnf"
    settings.write_text("[req]\ndistinguished_name=dn\n[dn]\nCN=localhost\n",encoding="ascii")
    subprocess.run([ssl_tool,"req","-config",str(settings),"-newkey","rsa:2048","-nodes","-x509","-days","1","-subj","/CN=localhost",
                    "-addext","subjectAltName=DNS:localhost","-addext","basicConstraints=critical,CA:TRUE",
                    "-addext","keyUsage=critical,digitalSignature,keyCertSign",
                    "-keyout",str(key),"-out",str(cert)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    http,game,public=three_ports()
    addr="https://localhost:"+str(public)
    config={"publicTls":addr,"caFile":str(cert)}
    def private(action,**fields):
        # Seed this isolated realm through its loopback operator endpoint. Public registration is tested separately.
        route={"url":"http://127.0.0.1:"+str(http)} if action=="register" else config
        return accounts.handle({"action":action,"server":route,**fields})
    env={k:v for k,v in os.environ.items() if not k.startswith("OF_")}
    env.update(OF_ACCOUNT_REGISTRATION="open", OF_HTTP_PORT=str(http),OF_GAME_PORT=str(game))
    state=temp/"realm-state"
    command=[sys.executable,"-B",str(server/"of.py"),"--state",str(state)]
    quiet={"cwd":server,"env":env,"stdout":subprocess.DEVNULL,"stderr":subprocess.DEVNULL}
    backend=subprocess.Popen(command+["start"],**quiet)
    try:
        wait_ready(lambda:json_call("http://127.0.0.1:"+str(http),"/healthz","GET"),backend)
        gate=subprocess.Popen(command+["player-gateway","--listen","127.0.0.1","--port",str(public),"--cert",str(cert),"--key",str(key)],**quiet)
        try:
            wait_ready(lambda:private("maintenance").get("ok"),gate)
            for bad in ({"publicTls":addr},{"publicTls":"https://127.0.0.1:"+str(public),"caFile":str(cert)}):
                try:accounts.request(bad,"GET","/local/v1/region")
                except ssl.SSLCertVerificationError:pass
                else:raise AssertionError("untrusted TLS server or hostname accepted")
            from test_launcher_window import trust_cases, exercise_window
            trust_cases(root, temp, addr, cert)
            if os.name=="nt":
                exercise_window(root, temp, cert, key)
            assert accounts.handle({"server":config,"action":"register","username":"captcha_test","password":"public-qa-password","nickname":""})["code"]=="captcha_required"
            who=dict(username="player_test",password="single-use-test-password")
            assert private("register",nickname="测试玩家",**who)["ok"]
            logged=private("login",**who)
            assert logged["ok"] and logged["nickname"]=="测试玩家"
            assert private("gifts")["ok"]
            assert accounts.request(config,"GET","/private/plaza")[0]==200
            local=LocalIdentity(temp/"player.sqlite")
            remote=RemotePassword(local,PlayerTlsChannels(addr,str(cert)),18881,11033)
            grant=remote.reserve(logged["slot"],logged["ticket"])
            try:remote.reserve(logged["slot"],logged["ticket"])
            except ValueError:pass
            else:raise AssertionError("private ticket was reusable")
            with remote.connect(grant) as player:
                conn=player.stream
                conn.sendall(encode(MsgId.PlayerLoginReq,b"",2))
                header,body=receive(conn)
                login=pb.PlayerLoginRsp.FromString(body)
                assert header.msg_id==MsgId.PlayerLoginRsp and login.player_name=="测试玩家"
                conn.sendall(encode(MsgId.PlayerMainDataReq,b"",3))
                for _ in range(128):
                    header,body=receive(conn)
                    if header.msg_id==MsgId.PlayerMainDataRsp:
                        main=pb.PlayerMainDataRsp.FromString(body)
                        assert main.player_id==1000001 and main.player_name=="测试玩家"
                        break
                else:raise AssertionError("missing role data over public game TLS")
            # Reconnect this authenticated role through the same packaged game adapter.
            sys.path.insert(0,str(Path(__file__).resolve().parent))
            from test_player_remote_features import run as features
            resumed=remote.resume(grant)
            assert resumed.slot==grant.slot and resumed.resume_token!=grant.resume_token
            print("Checking same-role TLS reconnect and authenticated native gameplay",flush=True)
            features(remote,resumed,state,config,accounts)
            print("Checking private backup preview, apply and undo",flush=True)
            over_tls(accounts,private,config,archive,who,state)
            assert accounts.request(config,"GET","/private/maintenance")[0]==200
        finally:
            gate.terminate();gate.wait(10)
    finally:
        if backend.poll() is None:
            subprocess.run(command+["stop"],**quiet,check=True)
            backend.wait(12)
    print("PASS packaged WPF launcher and in-game picker, cross-folder staging, guarded uninstall and retained original, TLS trust/hostname, registration/login/one-use ticket, gifts/plaza, UID 1000001, GM chat/announcement, nickname, photo upload/search and stripped image metadata, player-owned import preview/apply/undo over TLS.")


def verify(archive):
    with TemporaryDirectory(prefix="of-player-qa-") as name:
        test=Path(name);root=test/"Player package"
        with zipfile.ZipFile(archive) as src:
            keys=src.namelist()
            assert len(keys)==len(set(keys))
            assert not any(part in ("/"+key+"/") for key in keys for part in ("/Client/","/.git/","/__pycache__/"))
            assert not any(p.lower().endswith((".bundle",".unity3d",".sqlite",".sqlite3",".log",".pem",".key",".pfx",".pcap",".db",".dmp",".iso")) for p in keys)
            src.extractall(root)
        info=json.loads((root/"player-files.json").read_bytes())
        assert set(keys)==set(info["files"])|{".of-player-patch.json","player-files.json"}
        for rel,expected in info["files"].items():
            assert hashlib.sha256((root/rel).read_bytes()).hexdigest()==expected,rel
        binary=root/"OverField-Server/runtime-win64/python.exe"
        command=[str(binary),"-B",str(Path(__file__).resolve()),"--inside",str(root),str(test)]
        with subprocess.Popen(command) as worker:
            try:
                status=worker.wait(timeout=240)
            except subprocess.TimeoutExpired:
                if os.name=="nt":
                    subprocess.run(["taskkill","/PID",str(worker.pid),"/T","/F"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                if worker.poll() is None:worker.kill()
                raise
            if status:raise subprocess.CalledProcessError(status,command)


if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("--inside",nargs=2,type=Path)
    cli.add_argument("--archive",type=Path)
    cli.add_argument("--server-checkout",type=Path,help="OF_SF source tree for the shared import UI test")
    options=cli.parse_args()
    if options.server_checkout:os.environ["OF_SF_SERVER_CHECKOUT"]=str(options.server_checkout.resolve())
    if options.inside:inside(*options.inside)
    else:verify(options.archive)
