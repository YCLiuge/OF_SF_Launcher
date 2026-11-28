"""Prepare and update a private copy of the retail Unity player without touching its installation."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"OverField-Server/app"))
from client_build_catalog import BUILD, PATCH, NOVEMBER_PATCH as NOVEMBER_UPDATE, TAPTAP_RESOURCES, RESOURCE_SERIES, channel_records

from channel_policy import for_build
from native_endpoint import KNOWN_SOURCE, export_config, validate_endpoint
from private_channel_privacy import protect_private_channel_log

LAUNCHER = "a3702251bd92b7ba773b00eeffef7a63a311098e96e2e83f541e7ea78070001b"
ASSEMBLY = "2db98364241c95864781f77877523b9d2f728b22a91935fa30cdfb33a8b7f4ea"
RELEASE_ROOTS = {"launcher.exe","GameAssembly.dll","UnityPlayer.dll","baselib.dll","UnityCrashHandler64.exe","version"}
RELEASE_DIRS = {"il2cpp_data","Plugins","Resources","StreamingAssets","yoo"}
RELEASE_FILES = {"boot.config","ScriptingAssemblies.json","RuntimeInitializeOnLoads.json"}

def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()


def app_product(source):
    """Keep the retail product name (open-space vs OverField) used by YooAsset/saves."""
    try:
        lines=(Path(source)/"launcher_Data/app.info").read_text(encoding="utf-8-sig").splitlines()
        value=lines[1].strip() if len(lines)>1 else ""
        if value and len(value)<=64 and all(c.isprintable() for c in value):
            return value
    except (OSError,IndexError):
        pass
    return "开放空间"


def private_app_info(source):
    return ("OFPriv\n"+app_product(source)).encode("utf8")

MANIFESTS = {version: data.digest for version, data in TAPTAP_RESOURCES.patches.items()}
NOVEMBER_MANIFEST_SHA256 = MANIFESTS[NOVEMBER_UPDATE]
MANIFEST_DIR = Path("launcher_Data/yoo/DefaultPackage/ManifestFiles")
STEAM_APP_ID = 3879660


def plain_path(path):
    """Reject reparse points before reading a child; Path.resolve() would hide the link."""
    path = Path(path).absolute()
    if ".." in path.parts:
        raise ValueError("请选择不含上级跳转的实体安装目录。")
    for component in (*reversed(path.parents), path):
        try:
            info = component.lstat()
        except (FileNotFoundError, NotADirectoryError):
            break
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("安装路径包含链接或挂载点，请直接选择游戏的实体目录。")
    return path


def short_text(file, limit=128):
    file=plain_path(file)
    if not file.is_file() or file.stat().st_size>limit:
        return ""
    return file.read_bytes().decode("utf-8-sig",errors="replace").strip()


def record_for_build(build):
    return next((record for record in channel_records() if record["build"] == build), None)


def record_for_source(source, *, settings=True):
    version = short_text(source/"version")
    record = record_for_build(version)
    if record is None:
        return None
    fingerprints = {**record["files"], **record.get("verified_sdk", {})}
    if not settings:
        fingerprints.pop("launcher_Data/data.unity3d", None)
    for name, expected in fingerprints.items():
        file = plain_path(source / name)
        if not file.is_file() or sha(file) != expected:
            return None
    return record


def supported_manifest(source):
    folder = source / MANIFEST_DIR
    version = short_text(folder / "PackageManifest_DefaultPackage.version")
    expected = MANIFESTS.get(version)
    if expected is not None:
        manifest = plain_path(folder / ("PackageManifest_DefaultPackage_" + version + ".bytes"))
        return manifest.is_file() and sha(manifest) == expected
    record = record_for_build(short_text(source/"version"))
    if record is None:
        return False
    manifest = plain_path(folder / ("PackageManifest_DefaultPackage_" + version + ".bytes"))
    if not manifest.is_file():
        return False
    series = RESOURCE_SERIES.get(record["build"])
    patch = series.patches.get(version) if series else None
    if patch is not None:
        return sha(manifest) == patch.digest
    expected = record.get("bundled_manifest_sha256")
    if expected:
        return sha(manifest) == expected
    if series is not None:
        return sha(manifest) == series.base_digest
    return True


class RecognizedChannelError(ValueError):
    """Verified retail client identity with no tested private launch path."""


def verify_retail(source, *, settings=True):
    record = record_for_source(source, settings=settings)
    if record is not None:
        if not supported_manifest(source):
            raise ValueError("客户端的活动资源清单未通过验证。请在原渠道完成国服 1.1.12 更新。")
        return record
    version=short_text(source/"version")
    if version!=BUILD:
        value=version if re.fullmatch(r"[0-9.-]{1,40}",version) else "未知"
        raise ValueError(f"此客户端的构建 {value} 尚未验证。请使用兼容的国服 PC 1.1.12。")
    if not supported_manifest(source):
        raise ValueError("客户端的活动资源清单未通过验证。请在原渠道完成国服 1.1.12 更新。")
    files=[("launcher.exe",LAUNCHER),("GameAssembly.dll",ASSEMBLY)]
    if settings:
        files.append(("launcher_Data/data.unity3d",KNOWN_SOURCE))
    for name,expected in files:
        file=plain_path(source/name)
        if not file.is_file() or sha(file)!=expected:
            raise ValueError(f"{name} 的国服 PC 1.1.12 指纹不匹配，当前渠道版本尚未验证。")
    return None


def library_candidates(root, errors):
    yield root
    yield root/"PC Games"/"176228"
    if root.name.casefold()=="pc games":
        yield root/"176228"

    games=root/"Data/download/games"
    if root.name.casefold()=="data":games=root/"download/games"
    elif root.name.casefold()=="download":games=root/"games"
    elif root.name.casefold()=="games":games=root
    elif root.name.isascii() and root.name.isdecimal():yield root/"install"
    try:
        if plain_path(games).is_dir():
            # Only immediate numeric install slots from this library.
            for item in games.iterdir():
                if item.name.isascii() and item.name.isdecimal():
                    yield item/"install"
    except (ValueError,OSError) as error:
        errors.append(str(error))

    cache=root if root.name.casefold()=="steamapps" else root/"steamapps"
    common=root if root.name.casefold()=="common" else cache/"common"
    yield common/"OverField"
    try:
        acf=plain_path(cache/f"appmanifest_{STEAM_APP_ID}.acf")
        if acf.is_file() and acf.stat().st_size<=65536:
            value=acf.read_bytes().decode("utf-8-sig",errors="replace")
            if re.search(r'(?im)^\s*"appid"\s+"3879660"\s*$',value):
                match=re.search(r'(?im)^\s*"installdir"\s+"([^"]+)"\s*$',value)
                if match and re.fullmatch(r'[^<>:"/\\|?*\x00-\x1f]{1,120}',match[1]) and not match[1].endswith((".", " ")):
                    yield common/match[1]
    except (ValueError,OSError) as error:
        errors.append(str(error))


def installed_source(selected, *, required=True):
    root=plain_path(selected)
    matched=[]
    seen=set()
    errors=[]
    recognized=[]
    if root.is_dir():
        for candidate in library_candidates(root,errors):
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                exe=plain_path(candidate/"launcher.exe")
                if not exe.is_file() or not plain_path(candidate/"launcher_Data").is_dir():
                    continue
                verify_retail(candidate)
                matched.append(candidate)
                if candidate==root:
                    return root.resolve(strict=True)
            except RecognizedChannelError as error:
                if candidate==root:
                    raise
                recognized.append(error)
            except (ValueError,OSError) as error:
                errors.append(str(error))
    if len(matched)>1:
        raise ValueError("此库有多份通过校验的客户端，请直接选择要使用的游戏安装目录。")
    if matched:
        return matched[0].resolve(strict=True)
    if recognized:
        raise recognized[0]
    if errors:
        raise ValueError(errors[0])
    if required:
        raise ValueError("没有找到兼容的《开放空间》PC 安装。请选择游戏实体目录、渠道启动器目录或游戏库。")
    return None


def save(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
        tmp=Path(f.name)
        try:
            f.write((json.dumps(data,ensure_ascii=False,indent=2)+"\n").encode("utf-8"))
        except BaseException:
            f.close();tmp.unlink(missing_ok=True);raise
    try:
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)

def channel_runtime_state(relative):
    parts=Path(relative).as_posix().lower().split("/")
    return (parts[:4]==["launcher_data","plugins","win","pcsdk_release"] and
            len(parts)>4 and parts[4] in ("cache","logs","logs.txt"))

def asset_paths(src):
    dd=src/"launcher_Data"
    for item in sorted(RELEASE_ROOTS):
        yield plain_path(src/item)
    for item in sorted(RELEASE_FILES):
        yield plain_path(dd/item)
    for directory in sorted(RELEASE_DIRS):
        root=plain_path(dd/directory)
        if not root.is_dir():
            raise ValueError("游戏目录不完整，缺少 "+directory)
        for parent, dirs, files in os.walk(root,followlinks=False):
            base=Path(parent)
            dirs[:]=[d for d in dirs if not channel_runtime_state((base/d).relative_to(src))]
            for entry in dirs:
                candidate=base/entry
                if candidate.is_symlink() or getattr(candidate,'is_junction',lambda:False)():
                    raise ValueError("游戏资源树含目录链接，请选择实体安装。")
            for f in files:
                p=base/f
                if channel_runtime_state(p.relative_to(src)):continue
                if p.suffix.lower() in ('.log','.dmp','.tmp','.pcap'):continue
                if p.is_symlink():raise ValueError("游戏资源含文件链接。")
                yield p

BUILT = Path("launcher_Data/StreamingAssets/yoo/DefaultPackage")


def shared_bundle(relative):
    return relative.parent == BUILT and re.fullmatch(r"[a-f0-9]{32}\.bundle", relative.name) is not None


def compact_space(source, home, inputs, config):
    # Probe a real source bundle rather than trusting drive letters or assuming NTFS.
    bundle = next((path for path in inputs if shared_bundle(path.relative_to(source))), None)
    if bundle is None:
        raise ValueError("轻量补丁未找到此版本的只读资源包。")
    probe = home / (".of-link-check-" + uuid4().hex)
    try:
        os.link(bundle, probe)
    except OSError as error:
        raise ValueError("轻量补丁需要在原游戏同卷、支持硬链接的目录中运行。请选择同盘私服目录。") from error
    else:
        probe.unlink()
    partial = home / ".client-preparing"
    required = 3 * config.stat().st_size + 64 * 1024 * 1024
    for path in inputs:
        relative = path.relative_to(source)
        if shared_bundle(relative):
            continue
        existing = partial / relative
        size = path.stat().st_size
        if not existing.is_file() or existing.stat().st_size != size:
            required += size
    if shutil.disk_usage(home).free < required:
        raise ValueError(f"独立的程序和可写缓存需要至少 {required/(1024**3):.2f} GiB 可用空间。")
    return required


def _from_installed(source, home, endpoint, progress, *, compact_only=False):
    src=Path(source).resolve()
    home=Path(home).resolve()
    dest=home/"Client"
    endpoint=validate_endpoint(endpoint)
    if home.is_relative_to(src) or (src.is_relative_to(home) and src!=dest):
        raise ValueError("请选择与原游戏分开的联网私服工具目录。")
    home.mkdir(parents=True,exist_ok=True)
    meta_path=home/"client-config.json"
    old=json.loads(meta_path.read_bytes()) if meta_path.is_file() else {}
    baseline=home/"ClientSettings"/"data.unity3d"
    original=src/"launcher_Data/data.unity3d"
    record=record_for_build(short_text(src/"version"))
    channel=for_build(record["build"]) if record else None
    steam=bool(channel and channel.keep_official_data)
    source_launcher=record["files"]["launcher.exe"] if record else LAUNCHER
    source_assembly=record["files"]["GameAssembly.dll"] if record else ASSEMBLY
    source_config=record["files"]["launcher_Data/data.unity3d"] if record else KNOWN_SOURCE
    def settings_source():
        if baseline.exists():
            if sha(baseline)!=source_config:raise ValueError("独立原始配置文件校验失败。")
            return baseline
        if not original.is_file() or sha(original)!=source_config:
            raise ValueError("更改区服需要此版本原始 data.unity3d，请选择原版安装目录。")
        baseline.parent.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=baseline.parent,delete=False) as pending:
            temp=Path(pending.name)
        try:
            shutil.copyfile(original,temp)
            if sha(temp)!=source_config:raise ValueError("复制中的原始配置发生变化。")
            os.replace(temp,baseline)
        finally:
            temp.unlink(missing_ok=True)
        return baseline
    if dest.exists():
        if (not old or not (dest/".of-private-client.json").is_file()
            or json.loads((dest/".of-private-client.json").read_bytes()).get("instance")!=old.get("instance")):
            raise ValueError("现有 Client 目录不属于这份配置，请为此客户端选择独立位置。")
        if sha(dest/"launcher.exe")!=source_launcher or sha(dest/"GameAssembly.dll")!=source_assembly:
            raise ValueError("私服副本的原版程序已经变更。")
        app_info=dest/"launcher_Data/app.info"
        app_bytes=app_info.read_bytes()
        expected_info=private_app_info(src)
        if app_bytes!=expected_info:
            if app_bytes!=expected_info.replace(b"\n",b"\r\n"):
                raise ValueError("私服副本的 app.info 已由其他程序修改。")
            replacement=app_info.with_name(".app.info.new")
            replacement.write_bytes(expected_info)
            os.replace(replacement,app_info)
        package=dest/"launcher_Data/data.unity3d"
        checksum=sha(package)
        pending=home/".client-route.pending.json"
        stage=home/"ClientSettingsPending"/"next-route.unity3d"
        if pending.exists():
            update=json.loads(pending.read_bytes())
            if update["instance"]!=old["instance"]:
                raise ValueError("区服配置变更记录不匹配。")
            if checksum==update["sha256"]:
                old=update
                save(meta_path,old)
            elif checksum!=old["sha256"]:
                raise ValueError("区服配置在写入过程中被其他程序更改。")
            pending.unlink()
        if steam:
            # Steam keeps its signed/official data.unity3d; the channel mod
            # redirects the login endpoint in memory to preserve the Logo scene.
            if checksum != source_config:
                raise ValueError("Steam 副本的 data.unity3d 必须是官方原版，请重新准备。")
            if endpoint != old.get("url") or old.get("layout") != 2 or old.get("sha256") != checksum:
                updated = dict(old, url=endpoint, layout=2, sha256=checksum)
                save(meta_path, updated)
                old = updated
            if not baseline.is_file() and original.is_file():
                settings_source()
            return old
        if checksum!=old.get("sha256"):
            raise ValueError("私服副本的配置已由其他程序修改。")
        if endpoint!=old.get("url") or old.get("layout")!=2:
            baseline_file=settings_source()
            staged_hash=sha(stage) if stage.is_file() else None
            result=export_config(baseline_file,stage,endpoint,previous_sha256=staged_hash)
            updated=dict(old,url=endpoint,sha256=result["sha256"],layout=2)
            save(pending,updated)
            os.replace(stage,package)
            save(meta_path,updated)
            pending.unlink()
            old=updated
        elif not baseline.is_file() and original.is_file():
            settings_source()
        if not old.get("freshSdkCache"):
            # Early private test copies included a webview cache from the source install.
            # Separate it on the first update. Preserve this copy in the user's private state.
            sdk=dest/"launcher_Data/Plugins/Win/PCSDK_release"
            reserved=home/"State/previous-channel-cache"
            for name in ("cache","logs","logs.txt"):
                item=sdk/name
                if item.exists():
                    reserved.mkdir(parents=True,exist_ok=True)
                    if (reserved/name).exists():
                        raise ValueError("私服缓存迁移目标已存在，请先检查 State/previous-channel-cache。")
                    os.replace(item,reserved/name)
            old["freshSdkCache"]=True
            save(meta_path,old)
        return old
    if src==dest:
        raise ValueError("请先选择原版安装目录。")
    inputs=list(asset_paths(src))
    if compact_only:
        compact_space(src,home,inputs,original)
    original=settings_source()
    partial=home/".client-preparing"
    owner=partial/".of-private-client.json"
    if partial.exists():
        if not owner.is_file() or json.loads(owner.read_bytes()).get("source")!=str(src):
            raise ValueError("暂存目录已存在并属于其他安装。")
        meta=json.loads(owner.read_bytes())
    else:
        partial.mkdir()
        meta={"format":1,"instance":uuid4().hex,"source":str(src)}
        save(owner,meta)
    shared=copied=0
    for index,p in enumerate(inputs):
        if index%1000==0:progress("正在准备原版资源 %d / %d"%(index,len(inputs)),flush=True)
        rel=p.relative_to(src);out=partial/rel
        out.parent.mkdir(parents=True,exist_ok=True)
        if out.is_symlink():raise ValueError("暂存文件不能是符号链接。")
        length=p.stat().st_size
        if out.exists() and out.stat().st_size==length:
            if os.path.samefile(p,out):
                shared+=length
                continue
            if not (compact_only and shared_bundle(rel)):
                copied+=length
                continue
        if shared_bundle(rel):
            # Replace an interrupted earlier copy without ever writing to an official hard link.
            link=out.with_name(out.name+".of-link-"+uuid4().hex)
            try:
                os.link(p,link)
                os.replace(link,out)
                shared+=length
                continue
            except OSError as error:
                if compact_only:
                    raise ValueError("此目录无法硬链接原版资源。请在原游戏同一卷选择支持硬链接的目录。") from error
            finally:
                link.unlink(missing_ok=True)
        # Cache, executables, settings and DLLs are independent files.
        tmp=out.with_name(out.name+".of-partial")
        try:
            shutil.copyfile(p,tmp)
            if tmp.stat().st_size!=length:raise OSError("复制中的资源发生变化")
            os.replace(tmp,out);copied+=length
        finally:
            tmp.unlink(missing_ok=True)
    verify_retail(partial,settings=False)
    dd=partial/"launcher_Data"
    # app.info and the Unity PlayerSettings use the same private company for local saves.
    (dd/"app.info").write_bytes(private_app_info(src))
    (dd/"Quicksdk").mkdir(exist_ok=True)
    cfg=dd/"data.unity3d"
    if steam:
        shutil.copyfile(original,cfg)
        result={"url":endpoint,"sha256":sha(cfg),"bytes":cfg.stat().st_size}
    elif cfg.exists():
        if meta.get('sha256')!=sha(cfg):raise ValueError("暂存的原版派发配置发生变化。")
        result=export_config(original,cfg,endpoint,previous_sha256=meta['sha256'])
    else:
        result=export_config(original,cfg,endpoint)
    meta.update(layout=2,freshSdkCache=True,sha256=result["sha256"],url=endpoint,sharedBytes=shared,privateBytes=copied+cfg.stat().st_size)
    save(owner,meta)
    # If a previous process ended in between these writes the prepared directory still has its ownership marker.
    save(meta_path,meta)
    os.rename(partial,dest)
    return meta


def _media_entry(name):
    from pathlib import PurePosixPath
    rel=PurePosixPath(name)
    if rel.is_absolute() or "\\" in name or ":" in name or ".." in rel.parts or rel.as_posix()!=name:
        raise ValueError("安装媒体中有无效的资源路径。")
    parts=rel.parts
    if channel_runtime_state(rel.as_posix()):return False
    if len(parts)==1:
        return name in RELEASE_ROOTS
    if len(parts)==2:
        return parts[0]=="launcher_Data" and parts[1] in RELEASE_FILES
    return (parts[0]=="launcher_Data" and parts[1] in RELEASE_DIRS
            and rel.suffix.lower() not in (".log",".dmp",".tmp",".pcap"))


def _install_media(media,home,endpoint,progress):
    from client_media import open_media
    import binascii
    import stat
    endpoint=validate_endpoint(endpoint)
    partial=home/".client-preparing"
    owner=partial/".of-private-client.json"
    home.mkdir(parents=True,exist_ok=True)
    if partial.exists():
        if not owner.is_file():
            raise ValueError("现有暂存目录未标记为本次安装。")
        meta=json.loads(owner.read_bytes())
        if meta.get("source")!=str(media):
            raise ValueError("暂存目录的原版资源来源不同。")
    else:
        partial.mkdir()
        meta={"format":1,"instance":uuid4().hex,"source":str(media)}
        save(owner,meta)
    progress("正在校验原版安装媒体（约 25.8 GiB）。",flush=True)
    with open_media(media) as archive:
        if archive.read("version").decode().strip()!=BUILD or archive.read(
           "launcher_Data/yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version").decode().strip()!=PATCH:
            raise ValueError("安装媒体中游戏版本不正确。")
        items=[item for item in archive.infolist() if not item.is_dir() and _media_entry(item.filename)]
        copied=sum(i.file_size for i in items)
        remaining=sum(i.file_size for i in items if not (partial/i.filename).is_file())
        if shutil.disk_usage(home).free<remaining+250_000_000:
            raise ValueError("从安装媒体导入时，目标盘需要约 27 GiB 的可用空间。")
        def extract(item,output):
            if stat.S_ISLNK(item.external_attr>>16) or not output.resolve().is_relative_to(home):
                raise ValueError("安装媒体含非普通资源。")
            output.parent.mkdir(parents=True,exist_ok=True)
            if output.is_file() and output.stat().st_size==item.file_size:
                crc=0
                with output.open("rb") as existing:
                    for block in iter(lambda:existing.read(1024*1024),b""):
                        crc=binascii.crc32(block,crc)
                if crc==item.CRC:
                    return
            fd,tmp=tempfile.mkstemp(dir=output.parent,prefix=".of-media-")
            try:
                with os.fdopen(fd,"wb") as dst,archive.open(item) as src:
                    shutil.copyfileobj(src,dst,1024*1024)
                os.replace(tmp,output)
            finally:
                Path(tmp).unlink(missing_ok=True)
        baseline=home/"ClientSettings/data.unity3d"
        extract(archive.getinfo("launcher_Data/data.unity3d"),baseline)
        if sha(baseline)!=KNOWN_SOURCE:
            raise ValueError("安装媒体中的 Unity 配置文件校验失败。")
        for index,item in enumerate(items):
            if index%500==0:
                progress("正在从原版媒体安装游戏 %d / %d"%(index,len(items)),flush=True)
            extract(item,partial/item.filename)
    if sha(partial/"launcher.exe")!=LAUNCHER or sha(partial/"GameAssembly.dll")!=ASSEMBLY:
        raise ValueError("安装媒体中的 Unity 程序校验失败。")
    dd=partial/"launcher_Data"
    (dd/"app.info").write_bytes(private_app_info(media))
    (dd/"Quicksdk").mkdir(exist_ok=True)
    cfg=dd/"data.unity3d"
    if cfg.exists() and meta.get("sha256")!=sha(cfg):
        raise ValueError("安装暂存中的 Unity 配置已被修改。")
    result=export_config(baseline,cfg,endpoint,previous_sha256=meta.get("sha256"))
    meta.update(layout=2,freshSdkCache=True,sha256=result["sha256"],url=endpoint,sharedBytes=0,privateBytes=copied+cfg.stat().st_size)
    save(owner,meta)
    save(home/"client-config.json",meta)
    os.rename(partial,home/"Client")
    return meta


def prepare(source,home,endpoint,progress=print,*,compact_only=False):
    from client_media import locate
    src=plain_path(source)
    home=Path(home).resolve()
    dest=home/"Client"
    retail=installed_source(src,required=False) if src!=dest else None
    if dest.exists():
        # A migrated kit can run from its private copy when the original is no longer mounted.
        if retail is None and src not in (home,dest):
            config=home/"client-config.json"
            record=json.loads(config.read_bytes()) if config.is_file() else {}
            last_source=record.get("source")
            missing_original=not src.exists() and isinstance(last_source,str) and Path(last_source)==src
            if not missing_original and locate(src,home)!=src:
                raise ValueError("所选位置没有经过校验的客户端。请选择原游戏的实体目录或此副本的工作目录。")
        result=_from_installed(retail or dest,home,endpoint,progress,compact_only=compact_only)
    elif retail is not None:
        result=_from_installed(retail,home,endpoint,progress,compact_only=compact_only)
    else:
        if compact_only:
            raise ValueError("轻量玩家补丁需要本机已安装的国服 PC 1.1.12。请选择原游戏目录。")
        media=locate(src,home)
        if media is None:
            raise ValueError("请选择兼容的游戏安装目录、渠道启动器目录或配套原版 ISO / ZIP 安装媒体。")
        result=_install_media(media,home,endpoint,progress)
    protect_private_channel_log(home)
    return result
