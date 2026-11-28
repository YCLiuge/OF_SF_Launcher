"""Launch the retail OverField client copy with the player's chosen private realm."""
import argparse
import contextlib
import ctypes
import http.client
import hashlib
import ipaddress
import json
import msvcrt
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parent))
from prepare_client import ASSEMBLY, BUILD, LAUNCHER, channel_records, plain_path, prepare, save, sha, short_text
from channel_policy import for_build
from native_endpoint import validate_endpoint
from realm_addresses import recent, ssh_address

VERIFY_URL="https://sdkapi-of.inutan.com/webapi/checkUserInfo"


def channel_name(source):
    """Return the channel short name used by the client login patch."""
    channel=for_build(short_text(source/"version"))
    return channel.name if channel else "taptap"

@contextlib.contextmanager
def changing(home):
    home.mkdir(parents=True,exist_ok=True)
    with (home/'.launcher.lock').open('a+b') as owner:
        owner.seek(0)
        try:msvcrt.locking(owner.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise ValueError('另一份启动器正在准备客户端。') from None
        try:yield
        finally:owner.seek(0);msvcrt.locking(owner.fileno(),msvcrt.LK_UNLCK,1)

def json_call(url,path,method,body=None):
    u=urlsplit(url);cls=http.client.HTTPSConnection if u.scheme=='https' else http.client.HTTPConnection
    extra={'context':ssl.create_default_context()} if u.scheme=='https' else {}
    c=cls(u.hostname,u.port,timeout=(20 if path in ("/local/v1/accounts/attach","/local/v1/accounts/prepare") else 4),**extra)
    try:
        c.request(method,path,body=(json.dumps(body).encode("utf8") if body is not None else b"{}") if method=='POST' else None,
                  headers={'Content-Type':'application/json','Connection':'close'})
        response=c.getresponse()
        raw=response.read(65537)
        if len(raw)>65536 or response.status!=200:raise ValueError("服务器未提供所需区服接口（HTTP %d）。"%response.status)
        return json.loads(raw)
    finally:c.close()

def remote_pending_ticket(url,account):
    """Reserve a remote launch ticket through the local gateway, without an official token."""
    receipt=json_call(url,"/local/v1/accounts/prepare","POST",
                      {"slot":account["slot"],"ticket":account["ticket"]})
    pending=receipt.get("pending")
    if receipt.get("ok") is not True or not isinstance(pending,str) or not pending.startswith(("oft_","ofh_")):
        raise ValueError("本机区服未返回可用的私服启动票据。")
    return pending

def loopback(host):
    if host=="localhost":return True
    try:return ipaddress.ip_address(host).is_loopback
    except ValueError:return False

def is_running(home):
    try:
        pid=int((home/'.launcher.pid').read_text().strip())
    except (ValueError,OSError):return False
    import ctypes.wintypes as wt
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=[wt.DWORD,wt.BOOL,wt.DWORD];k.OpenProcess.restype=wt.HANDLE
    k.QueryFullProcessImageNameW.argtypes=[wt.HANDLE,wt.DWORD,wt.LPWSTR,ctypes.POINTER(wt.DWORD)]
    k.CloseHandle.argtypes=[wt.HANDLE]
    h=k.OpenProcess(0x1000,False,pid)
    if not h:return False
    try:
        k.GetExitCodeProcess.argtypes=[wt.HANDLE,ctypes.POINTER(wt.DWORD)]
        code=wt.DWORD()
        if not k.GetExitCodeProcess(h,ctypes.byref(code)) or code.value!=259:return False
        buf=ctypes.create_unicode_buffer(32768);n=wt.DWORD(len(buf))
        return bool(k.QueryFullProcessImageNameW(h,0,buf,ctypes.byref(n))) and Path(buf.value).resolve()==(home/'Client/launcher.exe').resolve()
    finally:k.CloseHandle(h)

def close_player(home):
    """Ask only the live, launcher-owned Unity window to close. Never force-terminate it."""
    if not is_running(home):
        return
    import ctypes.wintypes as wt
    pid = int((home/".launcher.pid").read_text().strip())
    user = ctypes.WinDLL("user32",use_last_error=True)
    proc = ctypes.WINFUNCTYPE(wt.BOOL,wt.HWND,wt.LPARAM)
    user.EnumWindows.argtypes=[proc,wt.LPARAM]
    user.GetWindowThreadProcessId.argtypes=[wt.HWND,ctypes.POINTER(wt.DWORD)]
    user.GetWindow.argtypes=[wt.HWND,wt.UINT];user.GetWindow.restype=wt.HWND
    user.IsWindowVisible.argtypes=[wt.HWND]
    user.PostMessageW.argtypes=[wt.HWND,wt.UINT,wt.WPARAM,wt.LPARAM]
    sent = []
    @proc
    def visit(window,unused):
        owner=wt.DWORD()
        user.GetWindowThreadProcessId(window,ctypes.byref(owner))
        if owner.value==pid and user.IsWindowVisible(window) and not user.GetWindow(window,4):
            kind=ctypes.create_unicode_buffer(128)
            title=ctypes.create_unicode_buffer(128)
            user.GetClassNameW(window,kind,len(kind))
            user.GetWindowTextW(window,title,len(title))
            if kind.value!="UnityWndClass" and title.value!="开放空间":
                return True
            if user.PostMessageW(window,0x10,0,0):
                sent.append(True)
                return False
        return True
    user.EnumWindows(visit,0)
    if sent:
        for _ in range(120):
            if not is_running(home):
                return
            time.sleep(.1)
    raise ValueError("请在游戏窗口确认并正常退出，再点击「应用并重启」。")


def remote_settings(args):
    if getattr(args, "public_tls", None):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"OverField-Server"/"app"))
        from player_tls_channels import player_address
        if args.remote_ssh or args.remote_profile or args.local_server:
            raise ValueError("公开玩家地址只能单独使用。")
        if args.action in ("launch", "relaunch") and not args.account_stdin:
            raise ValueError("请先登录所选区服的私服账号。")
        return {"publicTls": player_address(args.public_tls), "caFile":str(args.ca_file.resolve()) if args.ca_file else None}
    target=(args.remote_ssh or "").strip()
    if not target:
        if args.remote_profile or args.remote_ssh_port or args.remote_pair_first:
            raise ValueError("远端配置缺少 SSH 目标。")
        return {}
    target, port = ssh_address(target,args.remote_ssh_port)
    if getattr(args,"account_stdin",False) or (not args.remote_profile and getattr(args,"action","launch") in ("prepare","bookmark")):
        return {"remoteSsh":target,"remoteSshPort":port,"remotePairFirst":False}
    if not args.remote_profile:
        raise ValueError("请先登录该 SSH 区服的私服账号，或选择服主导出的个人档案。")
    if not args.remote_profile.is_file() or args.remote_profile.stat().st_size>16384:
        raise ValueError("请选择远端私服为你导出的个人档案 JSON。")
    profile=args.remote_profile.resolve()
    try:
        public=json.loads(profile.read_bytes())
        if public.get("realm")!="overfield-local-v1" or not re.fullmatch(r"ofp_[0-9a-f]{32}",public.get("slot","")):
            raise ValueError()
    except (AttributeError,TypeError,ValueError):
        raise ValueError("所选文件与远端区服个人档案格式不匹配。") from None
    return {"remoteSsh":target,"remoteProfile":str(profile),"remoteSshPort":port,
            "remotePairFirst":bool(args.remote_pair_first),"slot":public["slot"]}


def realm_state(home,remote):
    if not remote:
        return home/"State"/"realm"
    alias=([remote["publicTls"],"tls-native"] if "publicTls" in remote else [remote["remoteSsh"],remote["remoteSshPort"],remote.get("slot","native-accounts")])
    label=hashlib.sha256(json.dumps(alias).encode()).hexdigest()[:20]
    return home/"State"/("remote-"+label)


def managed_game_port():
    value=os.getenv("OF_LOCAL_GAME_PORT","11033")
    if not value.isascii() or not value.isdigit() or not 1<=int(value)<=65535:
        raise ValueError("本机玩法端口应在 1 到 65535 之间。")
    return int(value)


def revision(server,url,state,remote,account_native=False):
    version=hashlib.sha256()
    app=server/"app"
    # A new verified client manifest also changes the resource mirror contract.
    for file in sorted([*app.rglob("*.py"), *(app/"client_builds").glob("*.json")]):
        version.update(file.relative_to(app).as_posix().encode("utf8"))
        version.update(file.read_bytes())
    version.update((server/"of.py").read_bytes())
    version.update(b"preselected-realm-v3")
    version.update(json.dumps([url,str(state),remote,account_native,managed_game_port()],sort_keys=True).encode())
    return version.hexdigest()[:32]


def endpoint_status(url):
    try:
        reply=json_call(url,"/healthz","GET")
    except (ConnectionError,TimeoutError,http.client.RemoteDisconnected):
        return {}
    if reply.get("service")!="overfield-local-v1" or reply.get("nativeBuild")!="online-pc-1.1.12":
        raise ValueError("这个端口已被其他服务占用，请检查区服设置。")
    return reply


def stop_owned(server,state_dir,marker,old):
    url=old["url"]
    if not loopback(urlsplit(url).hostname):
        raise ValueError("启动器的服务状态文件包含无效的监听地址。")
    status=endpoint_status(url)
    if not status:
        return
    if not old.get("tag") or status.get("nativeSession")!=old["tag"]:
        raise ValueError("该端口的服务由其他程序启动，请关闭对应服务或使用其他端口。")
    state=Path(old["state"]).resolve()
    if state.parent!=state_dir or not re.fullmatch(r"(?:realm|remote-[a-f0-9]{20})",state.name):
        raise ValueError("启动器的区服状态路径不匹配。")
    subprocess.run([sys.executable,"-B",str(server/"of.py"),"--state",str(state),"stop"],
                   cwd=server,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                   creationflags=subprocess.CREATE_NO_WINDOW,check=True,timeout=6)
    for _ in range(60):
        if not endpoint_status(url):
            return
        time.sleep(.15)
    raise ValueError("上一个区服仍在保存，请稍后再试。")


def local_start(home,tools,src,url,remote,account_native=False,private_channel=False):
    # This adapter has been verified only for the national TapTap issuer.
    # Resource-only recognition of another channel cannot select an identity provider.
    version=short_text(src/"version")
    record=next((row for row in channel_records() if row["build"]==version),None)
    assembly = plain_path(src/"GameAssembly.dll")
    launcher = plain_path(src/"launcher.exe")
    if record is not None:
        expected_assembly=record["files"]["GameAssembly.dll"]
        expected_launcher=record["files"]["launcher.exe"]
    elif version==BUILD:
        expected_assembly=ASSEMBLY
        expected_launcher=LAUNCHER
    else:
        expected_assembly=expected_launcher=None
    if (expected_assembly is None or not assembly.is_file() or not launcher.is_file()
            or sha(assembly)!=expected_assembly or sha(launcher)!=expected_launcher):
        raise ValueError("此渠道尚未验证游戏账号服务，请从原渠道启动器进入。")

    u=urlsplit(url)
    if not loopback(u.hostname or "") or u.scheme!="http":
        raise ValueError("自动运行的本机登录服务请使用 http://127.0.0.1:端口。")
    state=realm_state(home,remote)
    state.parent.mkdir(exist_ok=True)
    marker=state.parent/"frontend.json"
    desired=revision(tools,url,state,remote,account_native)
    previous=json.loads(marker.read_bytes()) if marker.exists() else {}
    if previous and (previous.get("tag")!=desired or previous.get("url")!=url):
        stop_owned(tools,state.parent,marker,previous)
    status=endpoint_status(url)
    if status:
        if status.get("nativeSession")==desired:
            return
        raise ValueError("已有区服占用派发端口，请选择已有网关或先正常停服。")
    env={k:v for k,v in os.environ.items() if not k.startswith("OF_")}
    bind_host=urlsplit(url).hostname
    if bind_host is None or not loopback(bind_host):
        bind_host="127.0.0.1"
    env.update(OF_BIND=bind_host,OF_GAME_HOST="127.0.0.1",OF_CLIENT_ROOT=str(src),
               OF_HTTP_PORT=str(u.port or 80),OF_HTTP_HOSTS="localhost,127.0.0.1,"+bind_host,OF_DISPATCH_URL=url,
               OF_GAME_PORT=str(managed_game_port()),OF_STAGE_DIAGNOSTICS="1",OF_QUICKSDK_VERIFY_URL=VERIFY_URL,
               OF_QUICKSDK_CREATE_PLAYERS="1",OF_NATIVE_SESSION=desired,OF_NATIVE_PICKER="launcher",
               OF_ACCOUNT_REGISTRATION="open",OF_PRIVATE_CLIENT_EXE=str((src/"launcher.exe").resolve()))
    if account_native and not private_channel:
        env["OF_ACCOUNT_NATIVE"]="1"
    if remote.get("publicTls"):
        if not (account_native or private_channel):
            raise ValueError("公开区服须使用本服的私服账号。")
        env["OF_PLAYER_REALM_TLS"]=remote["publicTls"]
        if account_native and not private_channel:
            env["OF_REMOTE_ACCOUNT_NATIVE"]="1"
        if remote.get("caFile"):
            env["OF_PLAYER_REALM_CA"]=remote["caFile"]
    elif remote:
        env["OF_REMOTE_SSH_TARGET"]=remote["remoteSsh"]
        if account_native:
            env["OF_REMOTE_ACCOUNT_NATIVE"]="1"
        else:
            env["OF_REMOTE_PROFILE"]=remote["remoteProfile"]
            env["OF_REMOTE_PAIR_FIRST"]="1" if remote["remotePairFirst"] else "0"
        isolated_ssh=os.environ.get("OF_REMOTE_SSH_CONFIG")
        if isolated_ssh:
            env["OF_REMOTE_SSH_CONFIG"]=isolated_ssh
        if remote["remoteSshPort"]:
            env["OF_REMOTE_SSH_PORT"]=str(remote["remoteSshPort"])
    proc=subprocess.Popen([sys.executable,"-B",str(tools/"of.py"),"--state",str(state),"start"],
                          env=env,cwd=tools,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                          creationflags=subprocess.CREATE_NO_WINDOW,close_fds=True)
    try:
        for _ in range(120):
            if proc.poll() is not None:
                raise ValueError("本机服务启动失败，请检查玩法端口 %d、区服配置和游戏资源版本。"%managed_game_port())
            status=endpoint_status(url)
            if status:
                if status.get("nativeSession")!=desired:
                    raise ValueError("派发端口上的服务与本次配置不匹配。")
                save(marker,{"tag":desired,"url":url,"state":str(state)})
                return
            time.sleep(.25)
        raise ValueError("等待本机私服启动超时。")
    except BaseException:
        # This child failed before it issued a usable endpoint or accepted a game session.
        if remote.get("publicTls") and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
        raise


def preflight(url):
    info=json_call(url,'/dispatch/region_info','POST')
    if not info.get('status') or not info.get('is_server_open') or not info.get('gate_tcp_ip') or not 0<int(info.get('gate_tcp_port',0))<65536:
        raise ValueError('该地址未返回可用的《开放空间》玩法网关。')
    if not loopback(urlsplit(url).hostname) or not loopback(str(info['gate_tcp_ip'])):
        raise ValueError('当前原客户端使用正式渠道登录。请选择本机验证网关；远端玩法服需使用私服签发的档案接入。')
    return info

def display_arguments(mode):
    if mode == "game":
        return ["-window-mode", "borderless"]
    if mode == "borderless":
        return ["-window-mode", "borderless", "-screen-fullscreen", "1"]
    if mode == "window":
        return ["-screen-fullscreen", "0", "-screen-width", "1280", "-screen-height", "720"]
    raise ValueError("请选择有效的游戏显示模式。")


def main():
    if sys.stdout:sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    if sys.stderr:sys.stderr.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('prepare','launch','bookmark','relaunch'))
    parser.add_argument('--home',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--endpoint',required=True)
    parser.add_argument('--local-server',action='store_true')
    parser.add_argument('--account-stdin',action='store_true')
    parser.add_argument('--private-channel-login',action='store_true')
    parser.add_argument('--public-tls')
    parser.add_argument('--ca-file',type=Path)
    parser.add_argument('--compact-only', action='store_true')
    parser.add_argument('--display', choices=('game','borderless','window'), default='game')
    parser.add_argument('--remote-ssh')
    parser.add_argument('--remote-profile',type=Path)
    parser.add_argument('--remote-ssh-port',type=int)
    parser.add_argument('--remote-pair-first',action='store_true')
    args=parser.parse_args()
    remote=remote_settings(args)
    home=args.home.absolute();url=validate_endpoint(args.endpoint);src=args.source.absolute()
    if args.local_server and not loopback(urlsplit(url).hostname):raise ValueError('远程区服请取消“在本机运行区服”。')
    if not loopback(urlsplit(url).hostname):
        raise ValueError('异地的玩法服务需通过已配置的本机身份网关接入；请填写其回环派发地址。')
    account=None
    if args.account_stdin:
        if args.action not in ("launch","relaunch") or (not remote and not args.local_server):
            raise ValueError("请在本机自动或 SSH 区服模式使用私服账号进入。")
        raw=sys.stdin.buffer.read(8193)
        if len(raw)>8192:
            raise ValueError("私服登录会话无效。")
        try:account=json.loads(raw)
        except ValueError:raise ValueError("私服登录会话无效。") from None
        if not isinstance(account,dict) or not isinstance(account.get("ticket"),str) or not 16<=len(account["ticket"])<=2048:
            raise ValueError("私服登录会话无效。")
        if remote and not re.fullmatch(r"ofp_[0-9a-f]{32}",str(account.get("slot",""))):
            raise ValueError("远端私服账号会话不完整，请重新登录。")
    with changing(home):
        if args.action=="bookmark":
            recent(home,url,args.local_server,remote)
            print("服务器地址已保存，下次启动可选择。")
            return
        if args.action=="relaunch" and is_running(home):
            close_player(home)
        if is_running(home):raise ValueError("请先正常退出当前私服游戏窗口，再应用服务器设置。")
        meta=prepare(src,home,url,compact_only=True) if args.compact_only else prepare(src,home,url)
        for key in ('remoteSsh','remoteProfile','remoteSshPort','remotePairFirst','publicTls','caFile'):
            meta.pop(key,None)
        meta.update({k:v for k,v in remote.items() if k!='slot' and v is not None})
        meta['localServer']=bool(args.local_server and not remote)
        private_channel = bool(account is not None and args.private_channel_login)
        if args.action in ("launch","relaunch"):
            meta["privateAccountLogin"]=account is not None
            meta["privateChannelLogin"]=private_channel
        save(home/'client-config.json',meta)
        recent(home,url,args.local_server,remote)
        if args.action=='prepare':
            print('区服地址已保存。'+url);return
        if args.local_server or remote:
            print('正在启动本机登录服务。' if remote else '正在启动本机区服。',flush=True)
            local_start(home,home/'OverField-Server',home/'Client',url,remote,
                        account is not None, private_channel=private_channel)
        preflight(url)
        if private_channel and remote:
            account["ticket"]=remote_pending_ticket(url,account)
        print('区服已就绪，正在打开原版 Unity 客户端。',flush=True)
        game=home/'Client/launcher.exe'
        child_env=dict(os.environ)
        if private_channel:
            if not re.fullmatch(r"(?:oft|ofh)_[A-Za-z0-9_-]{43}",account["ticket"]):
                raise ValueError("私服登录票据格式无效，请重新登录私服账号。")
            channel=channel_name(src)
            child_env.update(OF_SF_SLOT=account["slot"], OF_SF_TICKET=account["ticket"],
                             OF_SF_NICKNAME=str(account.get("nickname","")),
                             OF_SF_CHANNEL=channel,
                             NO_PROXY="127.0.0.1,localhost,::1",
                             no_proxy="127.0.0.1,localhost,::1")
            if channel=="steam":
                # Steam keeps the official data.unity3d; redirect its in-memory
                # login config instead of rebuilding the signed Unity bundle.
                child_env["OF_SF_LOGIN"]=url
                child_env["OF_SF_CDN"]=url.rstrip("/")+"/Resources"
                child_env["OF_SF_YOO_MODE"]="2"
            print("正在使用私服票据直接进入区服，不打开官方渠道登录。",flush=True)
        proc=subprocess.Popen([str(game),*display_arguments(args.display)],cwd=game.parent,close_fds=True,
                              env=child_env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if account is not None and not private_channel:
            try:
                payload={"ticket":account["ticket"],"pid":proc.pid}
                if remote:payload["slot"]=account["slot"]
                receipt=json_call(url,"/local/v1/accounts/attach","POST",payload)
                if receipt.get("ok") is not True:raise ValueError("未能绑定此次进程。")
            except (OSError,ValueError,http.client.HTTPException):
                (home/'.launcher.pid').write_text(str(proc.pid),encoding='ascii')
                raise ValueError("本机区服未确认此私服账号与游戏进程的绑定。请正常退出游戏，重新登录私服账号再试。") from None
            finally:account.clear()
            print("该私服账号已绑定到本次原版游戏进程。",flush=True)
        elif account is not None:
            account.clear()
        (home/'.launcher.pid').write_text(str(proc.pid),encoding='ascii')
        time.sleep(.45)
        if proc.poll() is not None:raise ValueError('原版客户端已退出。请检查是否另有游戏实例在运行。')
        print('已打开游戏窗口。',flush=True)

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError,http.client.HTTPException) as exc:
        print(str(exc),file=sys.stderr);sys.exit(1)
