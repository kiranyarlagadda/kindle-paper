"""Build the demo edition and check the EPUB container is the shape readers expect."""

import contextlib
import io
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest import mock

import support  # noqa: F401  (sets up the import path and env)
import paper

OPF = "{http://www.idpf.org/2007/opf}"


class DemoEpub(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with mock.patch.object(paper, "DEMO_OUT", Path(cls.tmp.name)), \
                contextlib.redirect_stdout(io.StringIO()):
            path = paper.build_demo()
        cls.zip = zipfile.ZipFile(path)

    @classmethod
    def tearDownClass(cls):
        cls.zip.close()
        cls.tmp.cleanup()

    def test_mimetype_comes_first_uncompressed(self):
        first = self.zip.infolist()[0]
        self.assertEqual(first.filename, "mimetype")
        self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
        self.assertEqual(self.zip.read("mimetype"), b"application/epub+zip")

    def test_container_points_at_the_package(self):
        root = ET.fromstring(self.zip.read("META-INF/container.xml"))
        paths = [el.get("full-path") for el in root.iter() if el.get("full-path")]
        self.assertEqual(paths, ["OEBPS/content.opf"])

    def test_every_page_is_valid_xml(self):
        pages = [n for n in self.zip.namelist() if n.endswith(".xhtml")]
        self.assertGreater(len(pages), 5)
        for name in pages:
            with self.subTest(page=name):
                ET.fromstring(self.zip.read(name))

    def test_score_pages_sit_outside_the_reading_order(self):
        spine = ET.fromstring(self.zip.read("OEBPS/content.opf")).find(f"{OPF}spine")
        refs = {ref.get("idref"): ref.get("linear") for ref in spine}
        scores = [i for i in refs if i.startswith("score")]
        self.assertTrue(scores, "the demo edition has flagged games")
        for idref, linear in refs.items():
            with self.subTest(idref=idref):
                self.assertEqual(linear, "no" if idref in scores else None)


if __name__ == "__main__":
    unittest.main()
