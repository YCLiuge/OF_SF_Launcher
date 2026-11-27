"""Verify a selected realm and keep its trust material in this player's profile."""
from contextlib import closing
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"OverField-Server"/"app"))
sys.path.insert(0, str(ROOT/"client_tools"))
from player_tls_channels import PIN_FILE, PlayerTlsChannels, entered_address, player_address


def fingerprint(der):
    return hashlib.sha256(der).hexdigest()


def settings(root):
    path = root/"player-settings.json"
    stored = json.loads(path.read_bytes()) if path.is_file() else {}
    if not isinstance(stored, dict) or not isinstance(stored.get("servers", []), list):
        raise ValueError("原有连接设置格式无效。")
    return stored


def remember(root, address, ca):
    from prepare_client import save
    saved = settings(root)
    entry = {"publicTls":address, "caFile":ca}
    former = [row for row in saved.get("servers", []) if isinstance(row, dict) and row.get("publicTls") != address]
    saved.update(entry, servers=[entry, *former[:23]])
    save(root/"player-settings.json", saved)


def observed_leaf(channels):
    # This socket reads the public certificate only. No realm route, account data or HTTP is sent.
    inspect = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    inspect.minimum_version = ssl.TLSVersion.TLSv1_2
    inspect.check_hostname = False
    inspect.verify_mode = ssl.CERT_NONE
    with socket.create_connection((channels.host, channels.port), timeout=12) as tcp:
        with inspect.wrap_socket(tcp, server_hostname=channels.host) as tls:
            return tls.getpeercert(binary_form=True)


def validated_leaf(address, der, folder):
    name = folder/("of-leaf-"+fingerprint(der)+".pem")
    name.write_text(ssl.DER_cert_to_PEM_cert(der), encoding="ascii")
    # Validate expiry, TLS server purpose and SAN against that exact certificate before offering it.
    with PlayerTlsChannels(address, str(name)).verified_socket():
        pass
    return name


PUBLIC_ROOT = Path(__file__).with_name("certificates")/"sectigo-public-server-r46.crt"
PUBLIC_ROOT_SHA256 = "7bb647a62aeeac88bf257aa522d01ffea395e0ab45c73f93f65654ec38f25a06"

def public_root_path():
    # The SHA-256 is published in Microsoft's CCADB inclusion report.
    data = PUBLIC_ROOT.read_text(encoding="ascii")
    if fingerprint(ssl.PEM_cert_to_DER_cert(data)) != PUBLIC_ROOT_SHA256:
        raise ValueError("登陆器公有根证书摘要不匹配，请重新下载发行包。")
    return PUBLIC_ROOT.resolve()

def configured_public_root(ca):
    if not ca:
        return False
    # Recognize our own saved path when a portable directory has been moved.
    path = Path(ca)
    return (path.name == PUBLIC_ROOT.name and path.parent.name == "certificates"
            and path.parent.parent.name == "client_tools")

def verify_public_root(address):
    anchor = public_root_path()
    try:
        with PlayerTlsChannels(address, str(anchor)).verified_socket():
            return str(anchor)
    except ssl.SSLCertVerificationError as error:
        if error.verify_code in (18, 19, 20, 21):
            return ""
        raise


def read_catalog_page(channels, path, limit):
    with channels.verified_socket() as stream:
        with closing(http.client.HTTPConnection(channels.host, channels.port)) as client:
            client.sock = stream
            client.request("GET", path, headers={"Accept": "application/json", "Connection": "close"})
            with client.getresponse() as response:
                if response.status != 200:
                    raise ValueError("Catalog endpoint unavailable")
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise ValueError("Catalog response exceeds limit")
                return raw


def inspect_catalog(address, ca):
    import of_contracts as contracts
    channels = PlayerTlsChannels(address, ca)
    try:
        for _ in range(2):  # A hot publication can land between the two reads.
            meta = json.loads(read_catalog_page(channels, contracts.REALM_PATH, 16384).decode("utf-8"),
                              object_pairs_hook=contracts.unique_keys)
            if not isinstance(meta, dict) or meta.get("contract_id") != "ofsf.extensions.v1":
                return None
            if (meta.get("contract_version") != contracts.VERSION or meta.get("baseline") != "ofsf.native"
                    or meta.get("client_profile") != "ofsf.pc.native.v1"
                    or not isinstance(meta.get("capabilities"), list)
                    or "content.catalog.v1" not in meta["capabilities"]
                    or not isinstance(meta.get("content_manifest"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", meta["content_manifest"])):
                return None
            raw = read_catalog_page(channels, contracts.CONTENT_PATH, contracts.MAX_BYTES)
            if fingerprint(raw) == meta["content_manifest"]:
                catalog = json.loads(raw.decode("utf-8"), object_pairs_hook=contracts.unique_keys)
                contracts.validate(catalog)
                return len(catalog["items"])
    except (ValueError, OSError, http.client.HTTPException, RecursionError):
        pass
    return None


def selected_ca(root, address, override):
    stored = settings(root)
    matches = [row for row in [stored, *stored.get("servers", [])] if isinstance(row,dict) and row.get("publicTls") == address]
    ca = override or (str(matches[0].get("caFile") or "") if matches else "")
    return str(public_root_path()) if configured_public_root(ca) and not Path(ca).exists() else ca


def probe(root, form):
    from private_account_api import PRIVATE_REALM, request
    address = entered_address(form["address"])
    ca = selected_ca(root, address, form.get("caFile"))
    def read(anchor):
        start = time.perf_counter()
        status, region = request({"publicTls": address, "caFile": anchor}, "GET", "/local/v1/region", max_bytes=32768)
        elapsed = max(1, round((time.perf_counter() - start) * 1000))
        if status != 200 or region.get("realm") != PRIVATE_REALM:
            return {"ok": False, "code": "wrong_realm"}
        return {"ok": True, "latencyMs": elapsed}
    try:
        return read(ca)
    except ssl.SSLCertVerificationError as error:
        if ca or error.verify_code not in (18, 19, 20, 21):
            return {"ok": False, "code": "untrusted"}
    try:
        return read(str(public_root_path()))
    except ssl.SSLCertVerificationError:
        return {"ok": False, "code": "untrusted"}


def connect(root, form):
    from private_account_api import PRIVATE_REALM, request
    address = entered_address(form["address"])
    existing_ca = selected_ca(root, address, "")
    ca = form.get("caFile") or existing_ca
    renew = form.get("renew") is True and bool(existing_ca) and (PIN_FILE.fullmatch(Path(existing_ca).name) or configured_public_root(existing_ca))
    if renew and not form.get("caFile"):
        ca = ""
    if configured_public_root(ca) and not Path(ca).exists():
        ca = str(public_root_path())
    channels = PlayerTlsChannels(address, ca)
    try:
        with channels.verified_socket():
            pass
    except ssl.SSLCertVerificationError as err:
        if ca:
            return {"ok":False,"code":"trust_changed","message":"此区服与已保存的证书不符。请联系服主核实；证书确已更新时可在设置中重新核对。"}
        if err.verify_code not in (18,19,20,21):
            raise
        ca = verify_public_root(address)
        if not ca:
            der = observed_leaf(channels)
            digest = fingerprint(der)
            requested = form.get("fingerprint", "")
            if requested and requested != digest:
                return {"ok":False,"code":"trust_changed","message":"两次连接看到的证书不同。请重新向服主核实。"}
            with tempfile.TemporaryDirectory(prefix="ofsf-cert-") as tmp:
                leaf = validated_leaf(address, der, Path(tmp))
                if not requested:
                    return {"ok":False,"code":"fingerprint_required","fingerprint":digest,"address":address}
                cert_folder = Path(os.environ.get("LOCALAPPDATA", Path.home()))/"OF_SF"/"Certificates"
                cert_folder.mkdir(parents=True, exist_ok=True)
                chosen = cert_folder/leaf.name
                with tempfile.NamedTemporaryFile("wb", dir=cert_folder, delete=False) as out:
                    pending = Path(out.name)
                    out.write(leaf.read_bytes())
                try:
                    os.replace(pending,chosen)
                finally:
                    pending.unlink(missing_ok=True)
                ca = str(chosen)
    config = {"publicTls":address,"caFile":ca}
    status, region = request(config, "GET", "/local/v1/region", max_bytes=32768)
    if status != 200 or region.get("realm") != PRIVATE_REALM:
        return {"ok":False,"code":"wrong_realm","message":"此端口未提供兼容的 OF_SF 玩家区服。"}
    remember(root,address,ca)
    features=region.get("account_features") or []
    detail = {}
    if form.get("inspectCatalog") is True:
        count = inspect_catalog(address, ca)
        if count is not None:
            detail["catalogItems"] = count
    return {"ok":True, **detail, **config, "bundledPublicCa":configured_public_root(ca), "lookup":"captcha_lookup_v1" in features, "passwordChange":"password_change_v1" in features}


def main():
    form = json.loads(sys.stdin.buffer.read(16385))
    if not isinstance(form,dict) or not isinstance(form.get("address"),str) or len(form["address"])>2048:
        raise ValueError("请输入有效的区服地址。")
    if form.get("fingerprint") and not re.fullmatch(r"[0-9a-f]{64}",form["fingerprint"]):
        raise ValueError("指纹格式无效。")
    return probe(ROOT, form) if form.get("probe") is True else connect(ROOT, form)


if __name__ == "__main__":
    try:
        result = main()
    except ssl.SSLCertVerificationError:
        result = {"ok":False,"message":"证书的有效期、域名或信任链不匹配。请向服主核对地址及证书。"}
    except (ValueError, OSError, KeyError, TypeError):
        result = {"ok":False,"message":"连接失败。请核对 IP 或域名、端口及服务是否在线。"}
    print(json.dumps(result, ensure_ascii=True))
