"""Build the portable Windows player patch from a hydrated OF_SF source checkout."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

REPO = Path(__file__).resolve().parents[1]
PLAYER_MODULES = {"player_patch.py", "player_connection.py", "player_mod.py", "channel_login.py"}


def server_inputs(checkout):
    path = checkout/"tools/hydrate.py"
    if not path.is_file():
        path = Path(__file__).with_name("hydrate.py")
    if not path.is_file():
        raise ValueError("Choose the hydrated OF_SF server checkout with tools/hydrate.py")
    spec = importlib.util.spec_from_file_location("of_server_hydrate",path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compiler_arguments():
    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise ValueError("Install .NET SDK 9.0.306 to build the launcher")
    sdk_lines = subprocess.check_output([dotnet,"--list-sdks"], text=True).splitlines()
    sdk = next((Path(line.split("[",1)[1].rstrip("]"))/"9.0.306" for line in sdk_lines if line.startswith("9.0.306 [")), None)
    refs = Path(os.environ["ProgramFiles(x86)"])/"Reference Assemblies/Microsoft/Framework/.NETFramework/v4.8"
    if not sdk or not (refs/"PresentationFramework.dll").is_file():
        raise ValueError("Install .NET SDK 9.0.306 and .NET Framework 4.8 Developer Pack to build")
    libraries = ("mscorlib","System","System.Core","System.Security","System.Drawing","System.Windows.Forms","System.Web.Extensions",
                 "System.Xaml","WindowsBase","PresentationCore","PresentationFramework","System.Xml",
                 "System.IO.Compression","System.IO.Compression.FileSystem")
    return [dotnet,str(sdk/"Roslyn/bincore/csc.dll"),"/nologo","/noconfig","/nostdlib+","/deterministic+",
            "/langversion:7.3","/utf8output","/target:winexe","/platform:x64","/optimize+",
            *("/r:"+str(refs/(name+".dll")) for name in libraries)]


TITLE="OF_SF 登陆器.exe"
OUTPUT="OF_SF-Launcher-1.1.12-Windows-x64.zip"
MARKER=".of-player-patch.json"
INDEX="player-files.json"
CLIENT=("native_endpoint.py","client_media.py","native_launch.py","prepare_client.py","realm_addresses.py",
        "private_account_api.py","private_channel_privacy.py","player_patch.py","player_connection.py","player_mod.py",
        "channel_login.py")


def build(repo, output, *, artwork, server_checkout=None):
    repo=repo.resolve()
    checkout=(server_checkout or repo).resolve()
    inputs=server_inputs(checkout)
    SERVER, TEMPLATE = inputs.SERVER, inputs.TEMPLATE
    SERVER_TEMPLATE=inputs.SERVER_TEMPLATE
    output=output.resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    artwork=Path(artwork).resolve()
    if hashlib.sha256(artwork.read_bytes()).hexdigest()!="8dafb3280119a0d4e4613f360c6f3ab86f8fc43e89470d2c19e5dcbb6e25a4b0":
        raise ValueError("Expected poster112_绿荫小憩.png from the original artwork set")
    records=inputs.input_records(checkout)
    with tempfile.TemporaryDirectory(prefix=".of-player-",dir=output.parent) as work:
        temp=Path(work)
        exe=temp/TITLE
        version_source=str(repo/"client_tools/sources/ReleaseVersion.cs")
        subprocess.run([*compiler_arguments(),version_source,"/out:"+str(exe),
                        "/resource:"+str(artwork)+",Illustration.png",
                        "/resource:"+str(repo/"client_tools/sources/LauncherWindow.xaml")+",LauncherWindow.xaml",
                        str(repo/"client_tools/sources/PlayerPatch.cs"),str(repo/"client_tools/sources/RealmAccounts.cs"),
                        str(checkout/"client_tools/sources/ImportWindow.cs"),str(repo/"client_tools/sources/LauncherAccount.cs")],check=True)
        entries={TITLE:(exe,None),"玩家说明.md":(repo/"client_tools/PLAYER_PATCH.md",None)}
        entries.update({"client_tools/"+n:((repo if n in PLAYER_MODULES else checkout)/"client_tools"/n,None) for n in CLIENT})
        # Launcher-owned copies of the three-channel client policy and the two
        # modules that consume it override the server checkout.
        for name in ("prepare_client.py","native_launch.py","channel_policy.py"):
            local=repo/"client_tools"/name
            if local.is_file():entries["client_tools/"+name]=(local,None)
        entries["client_tools/channel_login/OFChannelLogin.dll"]=(repo/"client_tools/channel_login/OFChannelLogin.dll",None)
        entries["client_tools/channel_login/MelonLoader.x64.zip"]=(repo/"client_tools/channel_login/MelonLoader.x64.zip",None)
        entries["client_tools/channel_login/ChannelLoginMod.cs"]=(repo/"client_tools/channel_login/ChannelLoginMod.cs",None)
        entries["client_tools/channel_login/OFChannelLogin.csproj"]=(repo/"client_tools/channel_login/OFChannelLogin.csproj",None)
        root_cert = "client_tools/certificates/sectigo-public-server-r46.crt"
        entries[root_cert]=(repo/root_cert,None)
        entries["client_tools/native_endpoint_templates.json.bz2"]=(checkout/"client_tools/native_endpoint_templates.json.bz2",None)
        entries[TEMPLATE]=(checkout/TEMPLATE,inputs.TEMPLATE_SHA256)
        server=checkout/SERVER
        for key in ("of.py","LICENSE-of-ps.txt"):
            entries[SERVER+"/"+key]=(server/key,None)
        for path in [*(server/"app").rglob("*.py"), *(server/"app/client_builds").glob("*.json"), server/"app/notice_jpeg.ps1"]:
            if "__pycache__" not in path.parts:
                entries[path.relative_to(checkout).as_posix()]=(path,None)
        for name, record in records.items():
            if name.startswith(("resources/","runtime-win64/","vendor/common/","vendor/win64/")) or name==SERVER_TEMPLATE:
                entries[SERVER+"/"+name]=(inputs.local_path(server,name),record["sha256"])
        # Launcher-owned server channel policy and dispatch response overrides.
        for name in ("game_dispatch.py","channel_policy.py"):
            local=repo/"server_patch/OverField-Server/app"/name
            if local.is_file():entries["OverField-Server/app/"+name]=(local,None)
        target=temp/"player.zip"
        per_file={}
        with zipfile.ZipFile(target,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            def add(key, data):
                info=zipfile.ZipInfo(key)
                info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,data)
            for key,(path,expected) in sorted(entries.items()):
                if not path.is_file() or path.is_symlink():
                    raise ValueError("Expected plain input: "+key)
                data=path.read_bytes()
                digest=hashlib.sha256(data).hexdigest()
                if expected is not None and digest!=expected:
                    raise ValueError("Unexpected player build input: "+key)
                add(key,data)
                per_file[key]=digest
            settings_command = '@echo off\r\nchcp 65001 >nul\r\nstart "" "%~dp0OF_SF 登陆器.exe" --realm-picker\r\n'.encode("utf8")
            add("区服设置.cmd", settings_command)
            per_file["区服设置.cmd"] = hashlib.sha256(settings_command).hexdigest()
            add(MARKER,b'{"product":"OF_SF-Player-1.1.12"}\n')
            add(INDEX,json.dumps({"version":1,"files":per_file},ensure_ascii=False,sort_keys=True).encode())
        standalone=temp/"portable"/TITLE
        standalone.parent.mkdir()
        subprocess.run([*compiler_arguments(),version_source,"/out:"+str(standalone),"/resource:"+str(target)+",OF_SF.zip",
                        str(repo/"client_tools/sources/PortableEntry.cs")],check=True)
        executable=output.parent/TITLE
        digest_exe=hashlib.sha256(standalone.read_bytes()).hexdigest()
        exe_side=temp/"exe.sha256"
        exe_side.write_text(digest_exe+"  "+TITLE+"\n",encoding="utf8")
        digest=hashlib.sha256(target.read_bytes()).hexdigest()
        side=temp/"sha256"
        side.write_text(digest+"  "+output.name+"\n",encoding="utf8")
        os.replace(standalone,executable)
        os.replace(exe_side,executable.with_name(TITLE+".sha256"))
        os.replace(target,output)
        os.replace(side,output.with_name(output.name+".sha256"))
    return output


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,default=REPO,help="Launcher source checkout")
    parser.add_argument("--server-checkout",type=Path,required=True,help="Hydrated OF_SF source checkout")
    parser.add_argument("--output",type=Path,default=REPO/"release"/OUTPUT)
    parser.add_argument("--artwork",type=Path,required=True)
    options=parser.parse_args()
    print(build(options.repo,options.output,artwork=options.artwork,server_checkout=options.server_checkout))
