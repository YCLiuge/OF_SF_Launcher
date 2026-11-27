"""Check isolated optional module installation, retry and preservation of installed modules."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client_tools"))
import player_mod


def fixture(folder, name, contents):
    package = folder / name
    with zipfile.ZipFile(package, "w") as source:
        for key, value in contents.items():
            source.writestr(key, value)
    return package


class ModInstall(unittest.TestCase):
    def test_install_and_preserve(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = root / "Client"
            catalog = fixture(root, "catalog.zip", {"OfContractsModule.dll": b"catalog"})
            host = fixture(root, "host.zip", {"桂/Chat.Client.dll": b"chat", "桂/mod.dll": b"host"})
            manifest = client / "Mods/cache/manifest.json"
            manifest.parent.mkdir(parents=True)
            prior = {"modules": [{"id":"another", "enabled":True}], "channel":"stable"}
            manifest.write_text(json.dumps(prior), encoding="utf8")
            anchors = dict(HOST_HASH=player_mod.digest(host.read_bytes()), PACKAGE_HASH=player_mod.digest(catalog.read_bytes()),
                           MOD_HASH=player_mod.digest(b"host"), CHAT_HASH=player_mod.digest(b"chat"),
                           CATALOG_HASH=player_mod.digest(b"catalog"))
            with patch.multiple(player_mod, **anchors), patch("urllib.request.urlopen", side_effect=AssertionError("offline install contacted network")):
                original_manifest = manifest.read_bytes()
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    player_mod.install(client, host, host)
                self.assertFalse((client / "Mods/mod.dll").exists())
                self.assertEqual(manifest.read_bytes(), original_manifest)

                publish = player_mod.atomic_write
                def interrupted(path, data):
                    if path == manifest:
                        raise OSError("interrupted")
                    publish(path, data)
                with patch.object(player_mod, "atomic_write", interrupted), self.assertRaisesRegex(OSError, "interrupted"):
                    player_mod.install(client, catalog, host)
                self.assertFalse((client / "Mods/mod.dll").exists())
                self.assertEqual(manifest.read_bytes(), original_manifest)

                player_mod.install(client, catalog, host)
                player_mod.install(client)
                result = json.loads(manifest.read_bytes())
                self.assertEqual(result["channel"], "stable")
                self.assertEqual(result["modules"][0], prior["modules"][0])
                self.assertEqual([entry["id"] for entry in result["modules"]], ["another","ofsf_catalog"])
                dll = client / "Mods/mod.dll"
                dll.write_bytes(b"newer-upstream-host")
                with self.assertRaisesRegex(ValueError, "其他版本"):
                    player_mod.install(client, catalog, host)
                self.assertEqual(dll.read_bytes(), b"newer-upstream-host")


if __name__ == "__main__":
    unittest.main()
