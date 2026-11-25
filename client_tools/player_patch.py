"""Portable OF_SF player installation, own gateway lifecycle and reversible settings."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "client_tools"))
sys.path.insert(0,str(ROOT / "OverField-Server/app"))
from player_tls_channels import player_address
from prepare_client import save
from native_launch import changing, is_running, stop_owned

STAMP = ".of-player-patch.json"
PREFS = "player-settings.json"
MANIFEST = "player-files.json"
RUN_NAME = "OF_SF-Player-1.1.12"
EXE = "OF_SF 登陆器.exe"


def read(path, default=None):
    return json.loads(path.read_bytes()) if path.is_file() else default


def known_root(root):
    doc = read(root/STAMP, {})
    if doc.get("product") != "OF_SF-Player-1.1.12" or not (root/MANIFEST).is_file():
        raise ValueError("请选择已完整解压的 OF_SF 玩家补丁。")


def regular_root(root):
    if root.is_symlink() or getattr(root,"is_junction",lambda:False)():
        raise ValueError("请使用普通的玩家补丁目录。")


def manifest_entries(root):
    known_root(root)
    data = read(root/MANIFEST)
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("玩家补丁文件清单损坏。")
    for key, value in data["files"].items():
        rel = PurePosixPath(key)
        if (key != rel.as_posix() or rel.is_absolute() or ".." in rel.parts or
                any(ch in key for ch in "\\:") or not isinstance(value,str) or len(value) != 64):
            raise ValueError("玩家补丁清单包含无效文件。")
        yield key, value


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file,"sha256").hexdigest()


def choose_home(root, source, base):
    if not source.is_dir() or not (source/"launcher.exe").is_file():
        raise ValueError("请选择含 launcher.exe 的 TapTap 国服 1.1.12 安装目录。")
    if base:
        base = Path(base).resolve()
        if not base.is_dir():
            raise ValueError("请选择已存在的工作目录。")
        home = base/RUN_NAME
        if base == root and root.stat().st_dev == source.stat().st_dev:
            home = root
    else:
        home = root
    if (root.is_relative_to(source) or source.is_relative_to(root) or
            home.is_relative_to(source) or source.is_relative_to(home)):
        raise ValueError("私服工作目录必须与官服原安装分开。")
    if home.parent.stat().st_dev != source.stat().st_dev:
        raise ValueError("请在「工作位置」选择和原游戏同一磁盘的文件夹，以硬链接复用游戏资源。")
    regular_root(home)
    if home.exists() and home != root:
        if not (home/STAMP).is_file() or read(home/STAMP,{}).get("product")!="OF_SF-Player-1.1.12":
            raise ValueError("工作位置中存在其他同名文件夹，请更换工作位置。")
    return home


def stage(root, home):
    known_root(root)
    if root == home:
        return
    if not home.is_dir():
        home.mkdir()
        save(home/STAMP,read(root/STAMP))
    for name, expected in manifest_entries(root):
        src, dst = root/name, home/name
        if not dst.resolve().is_relative_to(home) or dst.is_symlink():
            raise ValueError("工作目录含意外的链接。")
        if dst.is_file() and digest(dst)==expected:
            continue
        if digest(src)!=expected:
            raise ValueError("补丁文件校验失败，请重新解压安装包。")
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(".of-copy-"+uuid.uuid4().hex)
        try:
            shutil.copyfile(src,tmp)
            if digest(tmp)!=expected:
                raise OSError("复制中的补丁文件校验失败。")
            os.replace(tmp,dst)
        finally:
            tmp.unlink(missing_ok=True)
    save(home/MANIFEST,read(root/MANIFEST))


WORK_STAMP = ".of-player-work.json"
GENERATED = ("Client",".client-preparing","ClientSettings","ClientSettingsPending","State",
             "client-config.json","server-addresses.json","player-ports.json",".launcher.pid",".client-route.pending.json")


def own_work(home, create=False):
    stamp=read(home/WORK_STAMP, {})
    if stamp.get("product")=="OF_SF-Player-1.1.12" and len(stamp.get("instance",""))==32:
        return True
    if any((home/name).exists() for name in GENERATED):
        raise ValueError("该目录已有其他程序的数据。请选择空的工作位置。")
    if create:
        save(home/WORK_STAMP,{"product":"OF_SF-Player-1.1.12","instance":uuid.uuid4().hex})
    return False


def endpoints(home):
    path=home/"player-ports.json"
    current=read(path,{})
    if all(type(current.get(k)) is int and 1024<=current[k]<=65535 for k in ("http","game")) and current["http"]!=current["game"]:
        return current
    with socket.socket() as a, socket.socket() as b:
        a.bind(("127.0.0.1",0))
        b.bind(("127.0.0.1",0))
        current=dict(http=a.getsockname()[1],game=b.getsockname()[1])
    save(path,current)
    return current


def stop_gateway(home):
    marker=home/"State"/"frontend.json"
    data=read(marker)
    if data:
        stop_owned(home/"OverField-Server",marker.parent,marker,data)


def watcher(home, pid):
    while (home/".launcher.pid").is_file() and (home/".launcher.pid").read_text().strip()==str(pid) and is_running(home):
        time.sleep(1)
    for _ in range(45):
        try:
            with changing(home):
                if not is_running(home):
                    stop_gateway(home)
            return
        except (ValueError,OSError):
            time.sleep(1)


def launch(home, prefs, account, relaunch=False):
    ports=endpoints(home)
    url="http://127.0.0.1:"+str(ports["http"])
    python=home/"OverField-Server/runtime-win64/python.exe"
    command=[str(python),"-B",str(home/"client_tools/native_launch.py"),"relaunch" if relaunch else "launch",
             "--home",str(home),"--source",prefs["source"],"--endpoint",url,"--public-tls",prefs["publicTls"],
             "--compact-only","--account-stdin"]
    if prefs["caFile"]:
        command.extend(["--ca-file",prefs["caFile"]])
    env=dict(os.environ,OF_LOCAL_GAME_PORT=str(ports["game"]),PYTHONUTF8="1")
    try:
        result=subprocess.run(command,input=json.dumps(account).encode(),env=env,cwd=home,check=False,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise ValueError("启动未完成，请查看上方提示。")
    finally:
        if is_running(home):
            pid=(home/".launcher.pid").read_text().strip()
            subprocess.Popen([str(python),"-B",str(home/"client_tools/player_patch.py"),"watch","--pid",pid],
                             cwd=home,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             close_fds=True,creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            with changing(home):
                stop_gateway(home)


def remove_workspace_tree(dest):
    def retry(func, name, error):
        if not isinstance(error, PermissionError) or os.name != "nt" or not Path(name).is_file():
            raise error
        from private_channel_privacy import _unlink_readonly_windows
        _unlink_readonly_windows(Path(name))
    shutil.rmtree(dest, onexc=retry)


def uninstall(home):
    known_root(home)
    if not own_work(home):
        return
    if is_running(home):
        raise ValueError("请先从游戏菜单正常退出此私服角色，再清除补丁。")
    with changing(home):
        stop_gateway(home)
        config=read(home/"client-config.json",{})
        for child in ("Client",".client-preparing"):
            dest=home/child
            if not dest.exists():
                continue
            regular_root(dest)
            stamp=read(dest/".of-private-client.json",{})
            if not stamp.get("instance") or (child=="Client" and stamp["instance"]!=config.get("instance")):
                raise ValueError("工作位置中的 Client 所有权不匹配，已保留。")
        for child in ("Client",".client-preparing","ClientSettings","ClientSettingsPending","State"):
            dest=home/child
            if dest.exists():
                regular_root(dest)
                remove_workspace_tree(dest)
        for name in ("client-config.json","server-addresses.json","player-ports.json",".launcher.pid",".client-route.pending.json", PREFS):
            (home/name).unlink(missing_ok=True)
        (home/WORK_STAMP).unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=("prepare","save","launch","relaunch","uninstall","watch"))
    parser.add_argument("--pid",type=int)
    args=parser.parse_args()
    known_root(ROOT)
    if args.action=="watch":
        watcher(ROOT,args.pid)
        return
    data=json.loads(sys.stdin.buffer.read(24577))
    prefs=read(ROOT/PREFS,{})
    if args.action=="uninstall":
        home=Path(prefs.get("home",str(ROOT))).resolve()
        # Accept only a work home made by this patch and matching the explicit source/working folder.
        if home!=ROOT and home != (Path(prefs["base"]).resolve()/RUN_NAME):
            raise ValueError("工作位置与已保存设置不符。")
        if home.exists() and (home/STAMP).is_file():
            uninstall(home)
        (ROOT/PREFS).unlink(missing_ok=True)
        print("已停止本补丁的服务并清除游戏副本和本机设置。可删除解压目录及独立工作目录。")
        return
    source=Path(data["source"]).resolve()
    home=choose_home(ROOT,source,data.get("base",""))
    address=player_address(data["publicTls"])
    ca_file=data.get("caFile","").strip()
    if ca_file:
        ca=Path(ca_file).resolve()
        if not ca.is_file() or ca.stat().st_size > 1024*1024:
            raise ValueError("请选择服主提供的 PEM 证书。")
        ca_file=str(ca)
    old_home=Path(prefs.get("home",str(home))).resolve()
    if old_home!=home and (old_home/"Client").is_dir():
        raise ValueError("要迁移已准备好的工作目录，请先用「清除补丁」回收旧副本。")
    record={"source":str(source),"base":str(data.get("base","")),"home":str(home),"publicTls":address,"caFile":ca_file}
    rows=[row for row in prefs.get("servers",[]) if row.get("publicTls")!=address]
    record["servers"]=[{"publicTls":address,"caFile":ca_file},*rows[:23]]
    if args.action=="save":
        save(ROOT/PREFS,record)
        print("已保存服务器地址与工作位置。")
        return
    stage(ROOT,home)
    with changing(home):
        own_work(home,create=True)
    save(ROOT/PREFS,record)
    if home!=ROOT:
        save(home/PREFS,record)
    if args.action=="prepare":
        from prepare_client import prepare
        with changing(home):
            if is_running(home):
                print("当前私服客户端正在运行。")
                return
            port=endpoints(home)["http"]
            result=prepare(source,home,"http://127.0.0.1:"+str(port),compact_only=True)
        print("文件已就绪，硬链接复用 %.2f GiB，副本独占 %.2f GiB。" %
              (result["sharedBytes"]/2**30,result["privateBytes"]/2**30))
    else:
        launch(home, record, data["account"], args.action=="relaunch")


if __name__=="__main__":
    if sys.stdout: sys.stdout.reconfigure(encoding="utf8",errors="replace")
    if sys.stderr: sys.stderr.reconfigure(encoding="utf8",errors="replace")
    try:
        main()
    except (OSError,ValueError,KeyError,RuntimeError,subprocess.SubprocessError) as error:
        print(str(error),file=sys.stderr)
        sys.exit(1)
