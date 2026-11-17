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

    def test_endline_quality_flags_and_coverage(self):
        config = pipeline.expand_env(pipeline.load_json(ROOT / "config" / "forms_endline.json"))
        raw = {
            role: pipeline.records_from_json(ROOT / "tests" / "fixtures" / "endline" / f"{role}.json")
            for role in ("students", "teachers", "directors")
        }
        targets = {
            "DEMO-001": {"school_id": "DEMO-001", "school_name": "Escuela Demo Norte", "province": "Mendoza", "students": 4, "teachers": 1, "directors": 1},
        }
        payload = pipeline.build_payload(raw, {}, config, targets, "unit-test-salt", True)
        students = {item["key"]: item for item in payload["forms"]}["students"]
        self.assertEqual(students["submissions"], 5)
        self.assertEqual(students["duplicate_submissions"], 1)
        self.assertEqual(students["linked_unique"], 3)
        self.assertEqual(
            students["quality"],
            {
                "linked_baseline": 4,
                "new_registration": 1,
                "incomplete": 1,
                "admin_deviation": 1,
                "knew_arm": 2,
                "no_consent": 0,
                "short_duration": 1,
                "very_long_duration": 1,
            },
        )
        teachers = {item["key"]: item for item in payload["forms"]}["teachers"]
        self.assertEqual(teachers["quality"]["no_consent"], 1)
        self.assertEqual(teachers["quality"]["short_duration"], 1)
        school = {row["school_id"]: row for row in payload["schools"]}["DEMO-001"]
        self.assertEqual(school["roles"]["students"]["target"], 4)
        self.assertEqual(school["roles"]["students"]["linked_unique"], 2)
        self.assertEqual(payload["grades"][0]["grade"], "3.º")
        self.assertTrue(payload["wave"]["has_quality_checks"])

    def test_endline_payload_publishes_no_identifiers_answers_or_arm(self):
        config = pipeline.expand_env(pipeline.load_json(ROOT / "config" / "forms_endline.json"))
        raw = {
            role: pipeline.records_from_json(ROOT / "tests" / "fixtures" / "endline" / f"{role}.json")
            for role in ("students", "teachers", "directors")
        }
        public_json = json.dumps(pipeline.build_payload(raw, {}, config, {}, "unit-test-salt", True), ensure_ascii=False)
        for forbidden in ("45999001", "20999001", "Nombre Ficticio", "TH-STU-0001", "NEW-STU-ZZ01", "saw_th_2026", "feel_safe", "th_recall", "study_arm", "treatment"):
            self.assertNotIn(forbidden, public_json)

    def test_actual_grade_coding(self):
        self.assertEqual(pipeline.split_grades("2", "students", "actual"), ["2.º"])
        self.assertEqual(pipeline.split_grades("4", "students", "actual"), ["4.º"])
        self.assertEqual(pipeline.split_grades("2", "students"), ["3.º"])

    def test_baseline_payload_has_no_quality_checks(self):
        config = pipeline.expand_env(pipeline.load_json(ROOT / "config" / "forms.json"))
        raw = {
            role: pipeline.records_from_json(ROOT / "tests" / "fixtures" / f"{role}.json")
            for role in ("students", "teachers", "directors")
        }
        payload = pipeline.build_payload(raw, {}, config, {}, "unit-test-salt", True)
        self.assertFalse(payload["wave"]["has_quality_checks"])
        self.assertIsNone(payload["forms"][0]["quality"])

    def test_grouped_xml_headers_are_resolved(self):
        row = {"identification/school_id": "ABC", "meta/student_id": "123"}
        self.assertEqual(pipeline.find_value(row, ["school_id"]), "ABC")
        self.assertEqual(pipeline.find_value(row, ["student_id"]), "123")

    def test_teacher_multiselect_grades(self):
        self.assertEqual(
            pipeline.split_grades("1 2 3 4 5", "teachers"),
            ["2.º", "3.º", "4.º", "Multigrado", "Otro"],
        )

    def test_student_grade_codes(self):
        self.assertEqual(pipeline.split_grades("1 2 3", "students"), ["2.º", "3.º", "4.º"])
        self.assertEqual(pipeline.split_grades("2DO 3RO 4TO", "students"), ["2.º", "3.º", "4.º"])

    def test_province_normalization(self):
        self.assertEqual(pipeline.normalize_province("  mendoza "), "Mendoza")
        self.assertEqual(pipeline.normalize_province("TUCUMAN"), "Tucumán")
        self.assertEqual(pipeline.normalize_province("la_rioja"), "La Rioja")
        self.assertEqual(pipeline.normalize_province("LA-RIOJA"), "La Rioja")
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
