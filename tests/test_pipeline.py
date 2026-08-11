import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_dashboard_data", ROOT / "scripts" / "build_dashboard_data.py")
pipeline = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(pipeline)


class PipelineTests(unittest.TestCase):
    def test_sample_metrics_and_privacy(self):
        config = pipeline.expand_env(pipeline.load_json(ROOT / "config" / "forms.json"))
        raw = {
            role: pipeline.records_from_json(ROOT / "tests" / "fixtures" / f"{role}.json")
            for role in ("students", "teachers", "directors")
        }
        payload = pipeline.build_payload(raw, {}, config, {}, "unit-test-salt", True)

        self.assertEqual(payload["summary"]["submissions"], 16)
        self.assertEqual(payload["summary"]["duplicate_submissions"], 3)
        self.assertEqual(payload["summary"]["missing_id"], 1)
        by_role = {item["key"]: item for item in payload["forms"]}
        self.assertEqual(by_role["students"]["unique_participants"], 6)
        self.assertEqual(by_role["teachers"]["duplicate_submissions"], 1)
        self.assertEqual(by_role["directors"]["duplicate_submissions"], 1)

        public_json = json.dumps(payload)
        self.assertNotIn("42001001", public_json)
        self.assertNotIn("28111001", public_json)
        self.assertNotIn("20101001", public_json)
        self.assertIn("EST-", public_json)

    def test_grouped_xml_headers_are_resolved(self):
        row = {"identification/school_id": "ABC", "meta/student_id": "123"}
        self.assertEqual(pipeline.find_value(row, ["school_id"]), "ABC")
        self.assertEqual(pipeline.find_value(row, ["student_id"]), "123")

    def test_teacher_multiselect_grades(self):
        self.assertEqual(
            pipeline.split_grades("1 2 3 4 5", "teachers"),
            ["2.º", "3.º", "4.º", "Multigrado", "Otro"],
        )

    def test_student_roster_grades_are_literal(self):
        self.assertEqual(pipeline.split_grades("1", "students"), ["1.º (fuera de alcance)"])
        self.assertEqual(pipeline.split_grades("2 3 4", "students"), ["2.º", "3.º", "4.º"])

    def test_province_normalization(self):
        self.assertEqual(pipeline.normalize_province("  mendoza "), "Mendoza")
        self.assertEqual(pipeline.normalize_province("TUCUMAN"), "Tucumán")
        self.assertEqual(pipeline.normalize_province(""), "Sin provincia")

    def test_minimal_xlsx_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.xlsx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "xl/workbook.xml",
                    '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="data" sheetId="1" r:id="rId1"/></sheets></workbook>',
                )
                archive.writestr(
                    "xl/_rels/workbook.xml.rels",
                    '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>',
                )
                archive.writestr(
                    "xl/worksheets/sheet1.xml",
                    '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>student_id</t></is></c><c r="B1" t="inlineStr"><is><t>school_id</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>123</t></is></c><c r="B2" t="inlineStr"><is><t>ABC</t></is></c></row></sheetData></worksheet>',
                )
            self.assertEqual(
                pipeline.read_xlsx_first_sheet(path),
                [{"student_id": "123", "school_id": "ABC"}],
            )


if __name__ == "__main__":
    unittest.main()
