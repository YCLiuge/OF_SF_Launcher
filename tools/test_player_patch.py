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


def source_cases(temp):
    temp=temp.resolve()
    import hashlib
    from unittest.mock import patch as mock
    import prepare_client as clients
    import player_patch

    assets = {"launcher.exe":b"retail", "GameAssembly.dll":b"assembly",
              "launcher_Data/data.unity3d":b"original"}
    manifest = b"verified test manifest"
    checksum = lambda value: hashlib.sha256(value).hexdigest()

    def install(root):
        for name, content in assets.items():
            (root/name).parent.mkdir(parents=True,exist_ok=True)
            (root/name).write_bytes(content)
        (root/"version").write_text(clients.BUILD,encoding="ascii")
        folder=root/clients.MANIFEST_DIR
        folder.mkdir(parents=True)
        (folder/"PackageManifest_DefaultPackage.version").write_text(clients.PATCH,encoding="ascii")
        (folder/("PackageManifest_DefaultPackage_"+clients.PATCH+".bytes")).write_bytes(manifest)
        return root

    def denied(action,word):
        try:
            action()
        except ValueError as error:
            assert word in str(error), error
        else:
            raise AssertionError("Incompatible game was accepted")

    with mock.multiple(clients,LAUNCHER=checksum(assets["launcher.exe"]),ASSEMBLY=checksum(assets["GameAssembly.dll"]),
                       KNOWN_SOURCE=checksum(assets["launcher_Data/data.unity3d"]),MANIFESTS={clients.PATCH:checksum(manifest)}):
        library=temp/"channel"
        games=library/"Data/download/games"
        game=install(games/"11113/install")
        unrelated=install(games/"42/install")
        (unrelated/"GameAssembly.dll").write_bytes(b"different game")
        (library/"launcher.exe").write_bytes(b"channel program")
        ignored=install(games/"900/cache/install")
        for folder in (library,library/"Data",library/"Data/download",games,game.parent,game):
            assert clients.installed_source(folder)==game
        assert ignored.is_dir()
        second=install(games/"100/install")
        denied(lambda:clients.installed_source(library),"多份")
        (second/"version").write_text("2025-03-21-17-15-05",encoding="ascii")
        denied(lambda:clients.installed_source(second),"2025-03-21")
        denied(lambda:clients.installed_source(unrelated),"GameAssembly.dll")
        # A measured channel build can be reported before a private login is available.
        observed=install(temp/"fixture-channel")
        channel_build="2025-12-31-23-59-59"
        (observed/"version").write_text(channel_build,encoding="ascii")
        report={"id":"fixture","build":channel_build,"channel":"样本渠道","publisher_version":"1.2.3",
                "files":{path:checksum(body) for path,body in assets.items()}}
        with mock.object(clients,"channel_records",return_value=(report,)):
            denied(lambda:clients.installed_source(observed),"已识别样本渠道 1.2.3")
            from native_launch import local_start
            scratch=temp/"uncreated-adapter"
            denied(lambda:local_start(scratch,scratch,observed,"http://127.0.0.1:18881",{}),"此渠道尚未验证")
            assert not scratch.exists()

            (observed/"GameAssembly.dll").write_bytes(b"different")
            denied(lambda:clients.installed_source(observed),channel_build)
        assert not (observed/".of-private-client.json").exists()

        assert clients.installed_source(library)==game

        tap=temp/"TapTap"
        retail=install(tap/"PC Games/176228")
        assert clients.installed_source(tap)==clients.installed_source(retail.parent)==retail
        steam=temp/"SteamLibrary"
        folder=steam/"steamapps"; steam_game=install(folder/"common/Fixture OverField")
        acf=folder/("appmanifest_"+str(clients.STEAM_APP_ID)+".acf")
        acf.write_text('"AppState"\n{\n "appid" "3879660"\n "installdir" "Fixture OverField"\n}',encoding="utf8")
        assert clients.installed_source(steam)==clients.installed_source(folder)==steam_game
        acf.write_text('"appid" "3879660"\n"installdir" "../elsewhere"',encoding="utf8")
        denied(lambda:clients.installed_source(steam),"没有找到")
        if os.name=="nt":
            import _winapi
            def link(where,target):_winapi.CreateJunction(str(target),str(where))
        else:
            def link(where,target):where.symlink_to(target,target_is_directory=True)
        alias=temp/"linked"; link(alias,game)
        denied(lambda:clients.installed_source(alias),"实体目录")
        junction=games/"1234"; link(junction,second.parent)
        denied(lambda:clients.installed_source(junction/"install"),"实体目录")
        assert clients.installed_source(library)==game
        # Skip alias entries before the temp tree is later removed.
        for alias in (alias,junction):
            alias.rmdir() if os.name=="nt" else alias.unlink()

        chosen=(game/clients.MANIFEST_DIR/("PackageManifest_DefaultPackage_"+clients.PATCH+".bytes"))
        chosen.write_bytes(b"unverified")
        denied(lambda:clients.prepare(game,temp/"uncreated","http://127.0.0.1:18881",compact_only=True),"资源清单")
        assert not (temp/"uncreated").exists()
        owned=temp/"existing";(owned/"Client").mkdir(parents=True)
        wrong=temp/"another game";wrong.mkdir()
        denied(lambda:clients.prepare(wrong,owned,"http://127.0.0.1:18881"),"经过校验")
        source=game/"launcher_Data/data.unity3d"; source.write_bytes(b"altered")
        chosen.write_bytes(manifest)
        denied(lambda:clients.installed_source(game),"data.unity3d")
        # Public player actions must reject the selected game before copying, saving or launching.
        import io
        payload=json.dumps({"source":str(game)}).encode()
        for action in ("save","prepare","launch","relaunch"):
            with mock.object(player_patch,"known_root"),mock.object(player_patch,"read",return_value={}),mock.object(sys,"argv",["patch",action]), \
                 mock.object(sys,"stdin",io.TextIOWrapper(io.BytesIO(payload))), \
                 mock.object(player_patch,"stage",side_effect=AssertionError("unexpected staged files")), \
                 mock.object(player_patch,"launch",side_effect=AssertionError("unexpected launch")):
                denied(player_patch.main,"data.unity3d")


def inside(root, temp):
    server=root/"OverField-Server"
    sys.path[:0]=[str(root/"client_tools"),str(server/"app"),str(server/"app/upstream")]
    from native_launch import display_arguments,json_call, is_running
    assert display_arguments("game")==["-window-mode","borderless"]
    assert display_arguments("borderless")==["-window-mode","borderless","-screen-fullscreen","1"]
    assert display_arguments("window")==["-screen-fullscreen","0","-screen-width","1280","-screen-height","720"]
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
    source_cases(temp/"source-fixture")
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
            who=dict(username="发行业务UP",password="single-use-test-password")
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
