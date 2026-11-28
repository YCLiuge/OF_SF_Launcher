"""Install the MelonLoader host and the OF_SF channel-login mod into a private client copy."""
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
MOD = HERE / "channel_login" / "OFChannelLogin.dll"
BUNDLED = HERE / "channel_login" / "MelonLoader.x64.zip"
MELONLOADER_URL = "https://github.com/LavaGang/MelonLoader/releases/download/v0.7.3/MelonLoader.x64.zip"
MELONLOADER_SHA256 = "5b2b2f3d1cd42b59ec886c5bdc2663edae87a0097a4f4a8f58c0965a99dda416"
MOD_SHA256 = "7366d7a4b3804602c3ebadcc4422c26561df5cfd6c2c6ffd0d65c19adb7c7669"
MOD_VERSION = "0.7.3"


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def safe_name(name):
    rel = PurePosixPath(name)
    if rel.is_absolute() or ".." in rel.parts or "\\" in name or ":" in name:
        raise ValueError("MelonLoader archive contains an invalid path.")
    return rel


def download(local_zip):
    if local_zip:
        source = Path(local_zip)
        data = source.read_bytes()
    else:
        request = urllib.request.Request(MELONLOADER_URL, headers={"User-Agent": "OF-SF-Launcher"})
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("MelonLoader 安装包过大。")
    if hashlib.sha256(data).hexdigest() != MELONLOADER_SHA256:
        raise ValueError("MelonLoader 安装包 SHA-256 不匹配。")
    return zipfile.ZipFile(io.BytesIO(data))


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".of-channel-")
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def ensure(client, local_zip=None, *, offline=False):
    client = Path(client).absolute()
    if not (client / "launcher.exe").is_file() or not (client / "GameAssembly.dll").is_file():
        raise ValueError("私服客户端目录不完整，请先准备游戏副本。")
    melon = client / "MelonLoader" / "net6" / "MelonLoader.dll"
    version = client / "version.dll"
    installed = False
    if not version.is_file() or not melon.is_file():
        if local_zip is None and BUNDLED.is_file():
            local_zip = BUNDLED
        if offline and not local_zip:
            raise ValueError("离线安装还需要选择 MelonLoader.x64.zip。")
        archive = download(local_zip)
        with archive:
            for member in archive.infolist():
                name = safe_name(member.filename)
                if name.name == "":
                    continue
                if name.as_posix() == "version.dll":
                    if not version.exists():
                        write_atomic(version, archive.read(member))
                elif name.parts[0] == "MelonLoader":
                    target = client.joinpath(*name.parts)
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        write_atomic(target, archive.read(member))
        installed = True
    if not (client / "MelonLoader" / "net6" / "MelonLoader.dll").is_file():
        raise ValueError("MelonLoader 安装不完整，请重新安装。")
    mod = client / "Mods" / "OFChannelLogin.dll"
    data = MOD.read_bytes()
    if hashlib.sha256(data).hexdigest() != MOD_SHA256:
        raise ValueError("登陆器内置的渠道登录模块校验失败。")
    if not mod.is_file() or digest(mod) != MOD_SHA256:
        write_atomic(mod, data)
    config = client / "UserData" / "Loader.cfg"
    if not config.is_file():
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "[loader]\n"
            "debug_mode = false\n"
            "disable_start_screen = true\n"
            "capture_player_logs = false\n"
            "\n[console]\n"
            "hide_console = true\n"
            "hide_warnings = true\n",
            encoding="utf-8",
        )
    return {"ok": True, "installed": installed, "version": MOD_VERSION}


if __name__ == "__main__":
    try:
        import json
        request = json.loads(sys.stdin.buffer.read(8193) or b"{}")
        result = ensure(request["client"], request.get("zip"), offline=bool(request.get("offline")))
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        result = {"ok": False, "message": str(error)}
    print(json.dumps(result, ensure_ascii=True))
