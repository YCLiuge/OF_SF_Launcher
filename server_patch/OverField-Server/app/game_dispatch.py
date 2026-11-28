"""1.1.12 public game discovery and a read-only, allowlisted local YooAsset mirror."""
import hashlib
import io
from threading import Lock
import ipaddress
import os
from pathlib import Path
import re
import socket
import struct
import time
from urllib.parse import urlsplit
from channel_policy import for_build
from local_picker import LABEL as PICKER_LABEL, LocalPicker


from client_build_catalog import BUILD, PATCH, NOVEMBER_PATCH, EVENING_PATCH, TAPTAP_RESOURCES, RESOURCE_SERIES

PATCH_BUNDLES = {version: data.plaza_bundle for version, data in TAPTAP_RESOURCES.patches.items()}
MANIFEST_SHA = {BUILD: TAPTAP_RESOURCES.base_digest, **{v: data.digest for v, data in TAPTAP_RESOURCES.patches.items()}}
VERSION = BUILD + "_" + PATCH
PREFIX = "PackageManifest_DefaultPackage"
PACKAGE = "DefaultPackage"
ROUTES = {"/dispatch/get_login_url_list", "/dispatch/client_hot_update", "/dispatch/region_info",
          "/dispatch/get_notice_list", "/dispatch/get_notice_url_list"}
HASH = re.compile(rb"\.bundle.{4}\x20\x00([a-f0-9]{32})\x08\x00[a-f0-9]{8}(.{8})", re.S)
CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _local_address(value):
    ip = ipaddress.ip_address(value)
    return (ip.is_loopback or ip.is_private or ip in CGNAT) and not (
        ip.is_unspecified or ip.is_multicast or ip.is_reserved and not ip.is_loopback
    )


def _origin(value):
    url = urlsplit(value)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username is not None
            or url.password is not None or url.path not in {"", "/"} or url.query or url.fragment
            or any(ord(c) < 33 or c in "\\;," for c in value)):
        raise ValueError("The game dispatch URL must be a dedicated private HTTP(S) origin")
    port = url.port
    addresses = {r[4][0] for r in socket.getaddrinfo(url.hostname, port or (443 if url.scheme == "https" else 80),
                                                       type=socket.SOCK_STREAM)}
    if not addresses or not all(_local_address(a) for a in addresses):
        raise ValueError("Advertise a loopback or private-network dispatch address")
    return f"{url.scheme}://{url.netloc}"


def _hash(path, algorithm):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def _issued_manifest(version, manifests, stock):
    if not re.fullmatch(rb"[2-4]\d{3}(-\d{2}){5}", version):
        return False
    name = f"{PREFIX}_{version.decode('ascii')}"
    try:
        expected = (stock / (name + ".sha256")).read_bytes().strip()
        if not re.fullmatch(rb"[0-9a-f]{64}", expected):
            return False
        blob = (manifests / (name + ".bytes")).read_bytes()
        return (hashlib.sha256(blob).hexdigest().encode() == expected
                and (manifests / (name + ".hash")).read_bytes().strip() == hashlib.md5(blob).hexdigest().encode())
    except FileNotFoundError:
        return False


def _stock_patch(installed,manifests,stock,private_copy,series=TAPTAP_RESOURCES):
    allowed={v.encode():v for v in series.patches}
    if installed in allowed:return allowed[installed]
    if private_copy and _issued_manifest(installed,manifests,stock):
        receipt=stock/(PREFIX+"_"+installed.decode("ascii")+".base")
        # Existing issued packages predate the receipt and used the November 7 manifest.
        base=receipt.read_bytes().strip() if receipt.exists() else (PATCH.encode() if series.build==BUILD else b"")
        if base in allowed:return allowed[base]
    raise ValueError("The expected active resource patch is unavailable")


class Assets:
    """Expose the original installed client files required by this specific active manifest."""

    def __init__(self, client_root):
        self.root = Path(client_root).resolve(strict=True)
        data = self.root / "launcher_Data"
        streaming = data / "StreamingAssets" / "yoo" / PACKAGE
        cache = data / "yoo" / PACKAGE
        self.build = (self.root / "version").read_text(encoding="ascii").strip()
        series = RESOURCE_SERIES.get(self.build)
        if series is None:
            raise ValueError("Unexpected client build")
        self._meta = {}
        self.plaza_lock = Lock()
        self.plaza_config = {}
        self.plaza_versions = {}
        self.stock = self.root / ".of-plaza-stock"
        self.private_copy = (self.root / ".of-private-client.json").is_file()
        version_file = cache / "ManifestFiles" / (PREFIX + ".version")
        # Before its first update, the Steam build has only its packaged manifest.
        if not version_file.exists() and self.build in series.patches:
            version_file = streaming / (PREFIX + ".version")
        installed = version_file.read_bytes().strip()
        self.patch=_stock_patch(installed,version_file.parent,self.stock,self.private_copy,series)
        digests = {self.build: series.base_digest, self.patch: series.patches[self.patch].digest}
        self.plaza_version=self.patch
        self._meta[version_file.name] = (version_file, _hash(version_file, "sha256"))
        active = None
        manifests = dict.fromkeys(((self.build, streaming), (self.patch, version_file.parent)))
        for version, base in manifests:
            manifest = base / f"{PREFIX}_{version}.bytes"
            if not manifest.exists() and version == self.patch and self.private_copy:
                manifest = self.stock / manifest.name
            checksum = manifest.with_suffix(".hash")
            blob = manifest.read_bytes()
            if hashlib.sha256(blob).hexdigest() != digests[version]:
                raise ValueError("Unexpected original manifest digest")
            if checksum.read_bytes().strip() != hashlib.md5(blob).hexdigest().encode():
                raise ValueError("Original manifest hash file mismatch")
            self._meta[manifest.name] = (manifest, digests[version])
            self._meta[checksum.name] = (checksum, _hash(checksum, "sha256"))
            if version == self.patch:
                active = blob
        self._bundles = {}
        missing = 0
        cached_count = 0
        entries = list(HASH.finditer(active))
        if len(entries) != series.patches[self.patch].bundle_count or len({e[1] for e in entries}) != len(entries):
            raise ValueError("Unexpected 1.1.12 manifest file table")
        stock_bundle = series.patches[self.patch].plaza_bundle
        for match in entries:
            name = match[1].decode("ascii")
            length, = struct.unpack("<Q", match[2])
            patch = cache / "CacheFiles" / name[:2] / name / "__data"
            packaged = streaming / (name + ".bundle")
            candidates = [patch, packaged]
            if self.private_copy and name == stock_bundle:
                candidates.append(self.stock / (name + ".bundle"))
            for path in candidates:
                try:
                    found = path.stat().st_size == length and path.is_file()
                except (FileNotFoundError, NotADirectoryError):
                    found = False
                if found:
                    self._bundles[name + ".bundle"] = (path, length, name)
                    cached_count += int(path == patch)
                    break
            else:
                missing += 1
        if missing:
            raise ValueError(f"Missing or wrong-sized active resource bundles: {missing} of {len(entries)}")
        self.count = len(self._bundles)
        self.from_cache = cached_count

    def select_plaza(self, config):
        from plaza_assets import build
        from realm_blessings import validate_plan
        series = RESOURCE_SERIES[getattr(self, "build", BUILD)]
        if not series.edit_plaza:
            return self.patch
        config = validate_plan(config, active_only=True)
        with self.plaza_lock:
            if config == self.plaza_config:
                return self.plaza_version
            if config:
                manifest_name = PREFIX + "_" + self.patch + ".bytes"
                stock_bundle=series.patches[self.patch].plaza_bundle
                bundle_name = stock_bundle + ".bundle"
                with self.open_stock(manifest_name) as src:
                    manifest = src.read()
                with self.open_stock(bundle_name) as src:
                    bundle = src.read()
                version, files = build(manifest, bundle, config, self.patch, PREFIX)
                if self.private_copy:
                    self.stock.mkdir(exist_ok=True)
                    if self.stock.resolve().parent != self.root:
                        raise ValueError("Invalid private stage backup directory")
                    # YooAsset may prune unreferenced cached assets. Preserve the three
                    # stock inputs needed for switching calendars and returning to automatic mode.
                    import tempfile
                    issued = f"{PREFIX}_{version}"
                    receipt = hashlib.sha256(files[issued + ".bytes"]).hexdigest().encode()
                    for name, content in ((manifest_name, manifest), (manifest_name[:-6]+".hash", hashlib.md5(manifest).hexdigest().encode()),
                                          (bundle_name, bundle), (issued+".base",self.patch.encode()), (issued+".sha256", receipt)):
                        target = self.stock / name
                        if target.is_file() and _hash(target,"sha256") == hashlib.sha256(content).hexdigest():
                            continue
                        with tempfile.NamedTemporaryFile(dir=self.stock,delete=False) as stream:
                            temporary = Path(stream.name)
                            stream.write(content)
                        try:
                            os.replace(temporary,target)
                        finally:
                            temporary.unlink(missing_ok=True)
                    # Continue serving the verified baseline even if a running player prunes its old cache.
                    self._meta[manifest_name] = (self.stock / manifest_name, series.patches[self.patch].digest)
                    hash_name = manifest_name[:-6] + ".hash"
                    self._meta[hash_name] = (self.stock / hash_name, _hash(self.stock / hash_name,"sha256"))
                    self._bundles[bundle_name] = (self.stock / bundle_name, len(bundle), stock_bundle)
                self.plaza_versions[version] = files
                if len(self.plaza_versions) > 2:
                    del self.plaza_versions[next(iter(self.plaza_versions))]
            else:
                version = self.patch
            self.plaza_version = version
            self.plaza_config = config
            return version

    def open(self, leaf):
        if leaf == PREFIX + ".version":
            return io.BytesIO(self.plaza_version.encode())
        with self.plaza_lock:
            for files in self.plaza_versions.values():
                if leaf in files:
                    return io.BytesIO(files[leaf])
        return self.open_stock(leaf)

    def open_stock(self, leaf):
        """Open only original manifest/version metadata or an active digest-named resource."""
        if leaf in self._meta:
            path, expected = self._meta[leaf]
            return self._verified_stream(path, expected, "sha256", None)
        if leaf not in self._bundles:
            raise FileNotFoundError(leaf)
        primary, size, digest = self._bundles[leaf]
        try:
            return self._verified_stream(primary, digest, "md5", size)
        except (OSError, ValueError):
            # The running Unity player can prune its cache after the mirror has mounted it.
            # Either local copy must satisfy the active manifest before any bytes are served.
            data = self.root / "launcher_Data"
            candidates = (
                data / "StreamingAssets" / "yoo" / PACKAGE / leaf,
                data / "yoo" / PACKAGE / "CacheFiles" / digest[:2] / digest / "__data",
            )
            for alternate in candidates:
                if alternate == primary:
                    continue
                try:
                    stream = self._verified_stream(alternate, digest, "md5", size)
                except (OSError, ValueError):
                    continue
                self._bundles[leaf] = (alternate, size, digest)
                return stream
            raise

    def _verified_stream(self, path, expected, algorithm, size):
        if not path.resolve(strict=True).is_relative_to(self.root):
            raise ValueError("Resource escapes client root")
        stream = path.open("rb")
        try:
            if size is not None and os.fstat(stream.fileno()).st_size != size:
                raise ValueError("Unexpected resource length")
            if hashlib.file_digest(stream, algorithm).hexdigest() != expected:
                raise ValueError("Resource content no longer matches the manifest")
            stream.seek(0)
            return stream
        except BaseException:
            stream.close()
            raise


class GameDispatch:
    def __init__(self, dispatch_url, game_host, game_port, *, client_root=None, region_name="本地区服"):
        self.origin = _origin(dispatch_url)
        if not isinstance(game_host,str) or not game_host or any(c.isspace() or c in "/\\?#@" for c in game_host):
            raise ValueError("Supply a private server gameplay hostname or IP")
        if game_host in ("0.0.0.0","::") or not 1 <= game_port <= 65535:
            raise ValueError("Supply the actual advertised gameplay endpoint")
        if not region_name or len(region_name) > 64 or any(ord(c)<32 for c in region_name):
            raise ValueError("Invalid region name")
        self.game_host, self.game_port = game_host, game_port
        self.region_name = region_name
        self.assets = None if client_root is None else Assets(client_root)
        self.picker = LocalPicker.configured(client_root,self.origin)
        self.maintenance = None

    def response(self, route):
        if route == "/dispatch/get_notice_url_list":
            return 200, {"data": []}
        if route == "/dispatch/get_notice_list":
            from realm_maintenance import native_notices
            import http.client, sqlite3
            try:
                rows=native_notices(self.maintenance.feed(),self.origin) if self.maintenance else []
            except (KeyError,TypeError,ValueError,OSError,sqlite3.Error,http.client.HTTPException):
                rows=[]
            return 200, {"data": rows, "serverTime": time.time_ns() // 1_000_000}
        build = getattr(self.assets, "build", BUILD)
        channel = for_build(build)
        if route == "/dispatch/get_login_url_list":
            # TapTap uses the dispatch URL list; Bilibili follows its own channel
            # launch path with an empty list. Steam expects an empty response body.
            mode = channel.login_url_mode if channel else ("tap" if build == BUILD else "empty_list")
            if mode == "empty":
                return 200, None
            if mode == "empty_list":
                return 200, {"LoginUrlList": []}
            return 200, {"LoginUrlList": [{"loginUrl": self.origin, "name": self.region_name}]}
        version = build+"_"+getattr(self.assets,"plaza_version",PATCH)
        if route in ("/dispatch/region_info", "/dispatch/client_hot_update") and self.assets and self.maintenance:
            if hasattr(self.maintenance, "plaza") and hasattr(self.assets, "select_plaza"):
                import http.client, sqlite3
                try:
                    version = build + "_" + self.assets.select_plaza(self.maintenance.plaza())
                except (OSError, ValueError, sqlite3.Error, http.client.HTTPException):
                    return 503, {"status": False, "message": "Private plaza resource plan unavailable"}
        if route == "/dispatch/region_info":
            return 200, {"status": True, "message": "success", "gate_tcp_ip": self.game_host,
                         "gate_tcp_port": self.game_port, "is_server_open": True, "text": "",
                         "client_log_tcp_ip": "", "client_log_tcp_port": 0, "currentVersion": version,
                         "photo_share_cdn_url": self.origin + "/private-game/photos"}
        if route == "/dispatch/client_hot_update":
            if self.assets is None:
                return 503, {"status": False, "message": "1.1.12 client assets are not mounted on this server"}
            steam = bool(channel and channel.steam)
            return 200, {"status": True, "message": "success", "hotOssUrl": self.origin + "/Resources",
                         "currentVersion": version, "server": "private", "server_id": "10001",
                         "ssAppId": "00000000000000000000000000000000" if steam else "",
                         "ssServerUrl": self.origin if steam else "",
                         "open_gm": False, "open_error_log": False,
                         "open_netConnecting_log": False, "ipAddress": "", "payUrl": "",
                         "isTestServer": False, "error_log_level": 2, "open_cs": False,
                         "serverTime": time.time_ns() // 1_000_000}
        raise KeyError(route)


def _send(handler, status, response):
    """Reply to discovery routes; None means the stock Steam empty body."""
    if response is not None:
        handler._reply(status, response)
        return
    handler.send_response(status)
    handler.send_header("Content-Length", "0")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.close_connection = True


def _resource(handler, dispatch, route):
    prefix = "/Resources/" + getattr(dispatch.assets, "build", BUILD) + "/"
    name = route[len(prefix):] if route.startswith(prefix) else ""
    if not name or "/" in name or "\\" in name or dispatch.assets is None:
        handler._reply(404, {"error": "resource not found"})
        return
    try:
        stream = dispatch.assets.open(name)
    except FileNotFoundError:
        handler._reply(404, {"error": "resource not found"})
        return
    except (OSError, ValueError):
        handler._reply(503, {"error": "original resource unavailable or changed"})
        return
    with stream:
        size = stream.seek(0, 2)
        first, last, status = 0, size - 1, 200
        ranges = handler.headers.get_all("Range") or []
        if ranges:
            m = re.fullmatch(r"bytes=([0-9]{1,19})-([0-9]{0,19})", ranges[0])
            if len(ranges) != 1 or m is None:
                handler._reply(416, {"error":"unsupported range"})
                return
            first = int(m[1])
            last = min(int(m[2]),size-1) if m[2] else size-1
            if not 0 <= first <= last < size:
                handler._reply(416, {"error":"range outside resource"})
                return
            status = 206
        handler.send_response(status)
        handler.send_header("Content-Type","application/octet-stream")
        handler.send_header("Content-Length",str(last-first+1))
        handler.send_header("Accept-Ranges","bytes")
        if status == 206:
            handler.send_header("Content-Range",f"bytes {first}-{last}/{size}")
        handler.send_header("Connection","close")
        handler.end_headers()
        handler.close_connection = True
        if handler.command == "HEAD":
            return
        stream.seek(first)
        remaining = last-first+1
        while remaining:
            block = stream.read(min(remaining,256*1024))
            if not block:
                raise IOError("resource changed during delivery")
            handler.wfile.write(block)
            remaining -= len(block)


def handle_game_dispatch(handler):
    """Call after ProfileHandler._accept() before reading any body. False means no matching route."""
    dispatch = getattr(handler.server,"game_dispatch",None)
    if dispatch is None:
        return False
    route = handler.path.partition("?")[0]
    # The retail 1.1.12 configuration uses this optional loopback mount.
    # Only public bootstrap routes are matched below, never player sessions.
    if route.startswith("/private-game/"):
        route = route[len("/private-game"):]
    picker = dispatch.picker
    if picker and route.startswith(picker.prefix + "/"):
        handler.close_connection = True
        suffix = route[len(picker.prefix):]
        if not picker.local_client(handler):
            handler._reply(403, {"error":"local player required"})
        elif handler.command != "POST" or suffix not in ROUTES:
            handler._reply(405, {"error":"unsupported address-editor operation"})
        else:
            # Retail confirms a selection by fetching its update plan. Keep that plan
            # valid so closing the editor leaves the current private realm playable.
            if suffix=="/dispatch/client_hot_update":
                try:
                    picker.enter(handler.connection)
                except OSError:
                    pass
            status, response = dispatch.response(suffix)
            _send(handler,status,response)
        return True
    if route not in ROUTES and not route.startswith("/Resources/"):
        return False
    handler.close_connection = True
    if any(key in handler.headers for key in ("Authorization","Proxy-Authorization","Cookie")):
        handler._reply(403, {"error":"credentials are not accepted by public game discovery"})
        return True
    if handler.command == "POST" and route in ROUTES:
        # The response is server-wide. Don't parse, log, echo, or persist request bodies.
        status, response = dispatch.response(route)
        if route == "/dispatch/get_login_url_list" and picker and picker.local_client(handler):
            if picker.preselected:
                # The PC launcher already selected and configured FinalLoginServer. An empty list
                # continues on that origin. Every nonempty list opens Unity's name + URL debug menu.
                response = {"LoginUrlList": []}
            elif response is not None:
                response["LoginUrlList"].append({"loginUrl":dispatch.origin + picker.prefix,"name":PICKER_LABEL})
        _send(handler,status,response)
    elif handler.command in ("GET","HEAD") and route.startswith("/Resources/"):
        _resource(handler,dispatch,route)
    else:
        handler._reply(405, {"error":"method not allowed"})
    return True

