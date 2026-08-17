import base64
import hashlib
import re
import shutil
import tempfile
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.test import Client, SimpleTestCase, override_settings


class _StaticIntegrityParser(HTMLParser):
    static_path_pattern = re.compile(r"^\{%\s*static\s+['\"]([^'\"]+)['\"]\s*%\}(?:\?.*)?$")

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.assets = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        integrity = attributes.get("integrity", "")
        asset_url = attributes.get("href") or attributes.get("src")
        if not integrity.startswith("sha384-") or not asset_url:
            return

        match = self.static_path_pattern.match(asset_url)
        if match:
            self.assets.append((match.group(1), integrity))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


class StaticAssetIntegrityTests(SimpleTestCase):
    def test_template_integrity_hashes_match_static_assets(self):
        checked_assets = 0
        templates_dir = settings.BASE_DIR / "templates"

        for template_path in templates_dir.rglob("*.html"):
            parser = _StaticIntegrityParser()
            parser.feed(template_path.read_text(encoding="utf-8-sig"))

            for relative_path, integrity in parser.assets:
                asset_path = settings.BASE_DIR / "static" / relative_path
                self.assertTrue(asset_path.is_file(), f"Asset ausente: {relative_path}")

                digest = hashlib.sha384(asset_path.read_bytes()).digest()
                expected_integrity = f"sha384-{base64.b64encode(digest).decode('ascii')}"
                self.assertEqual(
                    integrity,
                    expected_integrity,
                    f"Hash de integridade invalido em {template_path.name}: {relative_path}",
                )
                checked_assets += 1

        self.assertGreater(checked_assets, 0, "Nenhum asset com integridade foi encontrado nos templates.")


class MediaServingTests(SimpleTestCase):
    def setUp(self):
        self.media_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.media_dir, ignore_errors=True)

    def test_media_files_are_served_with_debug_disabled(self):
        media_root = Path(self.media_dir)
        qr_dir = media_root / "qrcodes"
        qr_dir.mkdir(parents=True)
        (qr_dir / "qr-test.png").write_bytes(b"fake-png")

        with override_settings(DEBUG=False, MEDIA_ROOT=media_root):
            response = Client().get("/media/qrcodes/qr-test.png")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"fake-png")
