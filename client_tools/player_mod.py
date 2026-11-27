"""Install the optional catalogue through overfiled-mod's public module cache format."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

HOST_COMMIT = "eac420fcacc4cb48e09746ef2b5f7ca760d00a76"
HOST_URL = f"https://raw.githubusercontent.com/jianx14/overfiled-mod/{HOST_COMMIT}/mod.zip"
PACKAGE_URL = "https://github.com/YCLiuge/OF_Contracts/releases/download/v0.1.0/OF_SF-ModCatalog-0.1.0-Windows.zip"
HOST_HASH = "ca023007efa1de20b4756341ce5f8728f639dff1615e179c2dd4eea43d8f14ba"
PACKAGE_HASH = "bfc9c7fcdb39a06ed1773dc0cb3ba910f234e816f49a8e5db9d19334d3c649e3"
MOD_HASH = "a9a9e445bd5f6108edebda969b0ba799fb60b8742c976bcaad2841147ee34e9a"
CHAT_HASH = "5b0e169235ec8d51ea469d4ca3165ece58ed859c22643582b4e89c6b7b77dcf0"
CATALOG_HASH = "05551ba97e7d35c593464b0212288fe49b3af35998acd87a94f06be8cc70ee05"
LIMIT = 20 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def archive(url, expected, local=None):
    if local:
        stream = Path(local).open("rb")
    else:
        request = urllib.request.Request(url, headers={"User-Agent": "OF-SF-Launcher"})
        stream = urllib.request.urlopen(request, timeout=15)
    with stream:
        if not local and not stream.geturl().startswith("https://"):
            raise ValueError("下载连接必须使用 HTTPS。")
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT or digest(data) != expected:
        raise ValueError("安装包 SHA-256 不匹配。请下载此版本对应的安装包。")
    return zipfile.ZipFile(io.BytesIO(data))


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def install(client, package=None, host=None):
    paths = [client / "Mods/mod.dll", client / "UserLibs/Chat.Client.dll",
             client / "Mods/cache/ofsf_catalog.dll", client / "Mods/cache/manifest.json"]
    mod, chat, catalog, manifest = paths
    for path in paths:
        for part in (path, *path.parents):
            if part == client.parent:
                break
            if part.is_symlink() or (part.exists() and part.stat(follow_symlinks=False).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise ValueError("Mod 目录含文件链接，请检查工作副本。")
    for path, expected in ((mod, MOD_HASH), (chat, CHAT_HASH)):
        if path.exists() and (path.stat().st_size > LIMIT or digest(path.read_bytes()) != expected):
            raise ValueError(f"{path.name} 是其他版本，请从项目主页核对兼容性。原文件已保留。")
    if manifest.exists() and manifest.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("本机模块清单过大。")
    state = json.loads(manifest.read_bytes()) if manifest.exists() else {"modules": []}
    if not isinstance(state, dict) or not isinstance(state.get("modules"), list) or not all(isinstance(row,dict) for row in state["modules"]):
        raise ValueError("已有模块清单格式错误。")
    entries = [row for row in state["modules"] if row.get("id") == "ofsf_catalog"]
    if len(entries) > 1:
        raise ValueError("已有清单包含重复的目录模块。")
    if entries and (entries[0].get("hash") != CATALOG_HASH or entries[0].get("version") != "0.1.0"):
        raise ValueError("现有目录模块是其他版本。请通过上游更新，已保留原版本。")
    if catalog.exists() and (catalog.stat().st_size > LIMIT or digest(catalog.read_bytes()) != CATALOG_HASH):
        raise ValueError("现有目录模块与本版不同，已保留原文件。")
    files = {}
    if not mod.exists() or not chat.exists():
        with archive(HOST_URL, HOST_HASH, host) as source:
            files[chat] = source.read("桂/Chat.Client.dll")
            files[mod] = source.read("桂/mod.dll")
    if not catalog.exists():
        with archive(PACKAGE_URL, PACKAGE_HASH, package) as source:
            files[catalog] = source.read("OfContractsModule.dll")
    module = dict(entries[0]) if entries else {}
    module.update(id="ofsf_catalog", version="0.1.0", hash=CATALOG_HASH, priority=120, enabled=True)
    if entries:
        state["modules"][state["modules"].index(entries[0])] = module
    else:
        state["modules"].append(module)
    added = []
    try:
        for path, data in files.items():
            if not path.exists():
                atomic_write(path, data)
                added.append(path)
        atomic_write(manifest, json.dumps(state, ensure_ascii=False, indent=2).encode())
    except OSError:
        for path in reversed(added):
            path.unlink(missing_ok=True)
        raise


def apply(form):
    import player_patch
    from native_launch import changing, is_running
    from prepare_client import prepare
    root = player_patch.ROOT
    source = player_patch.installed_source(form["source"])
    home = player_patch.choose_home(root, source, form.get("base"))
    prefs = player_patch.read(root / player_patch.PREFS, {})
    old_home = Path(prefs.get("home", str(home))).resolve()
    if old_home != home and (old_home / "Client").is_dir():
        raise ValueError("请先清除原工作位置的游戏副本，再更换工作目录。")
    client = home / "Client"
    player_patch.stage(root, home)
    with changing(home):
        player_patch.own_work(home, create=True)
        if is_running(home):
            raise ValueError("请退出此游戏副本后再安装扩展。")
        port = player_patch.endpoints(home)["http"]
        prepare(source, home, f"http://127.0.0.1:{port}", progress=lambda *args, **kwargs: None, compact_only=True)
        if not (client / "MelonLoader").is_dir():
            return {"ok": False, "code":"loader_required", "client":str(client / "launcher.exe")}
        if form.get("offline") and not form.get("host") and not all((client / name).is_file() for name in ("Mods/mod.dll","UserLibs/Chat.Client.dll")):
            raise ValueError("首次离线安装还需选择上游的 mod.zip。")
        install(client, form.get("package"), form.get("host"))
    return {"ok": True}


if __name__ == "__main__":
    try:
        result = apply(json.loads(sys.stdin.buffer.read(16385)))
    except (OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile) as error:
        result = {"ok": False, "message": str(error)}
    print(json.dumps(result, ensure_ascii=True))
