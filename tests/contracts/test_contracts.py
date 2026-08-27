import pytest
from pydantic import ValidationError

from ads_booster.contracts import CaptureJob, ComponentExportManifest

VALID_JOB = """
{
  "schema_version": "trace.capture-job.v1",
  "job_id": "20260822-jp-001",
  "context": {
    "country": "JP",
    "persona_id": "jp-university-student",
    "promotion_material_id": "exam-week-lockscreen"
  },
  "device": {
    "kind": "simulator",
    "udid": "E1FB798D-79E6-4B25-A987-D298A4FD122A",
    "platform_version": "26.5",
    "device_name": "iPhone 17 Pro"
  },
  "scenes": [
    {
      "scene_id": "lockscreen-01",
      "locale": "ja-JP",
      "capture_target": "trace_components",
      "background_image": "inputs/backgrounds/exam-week.jpg",
      "trace_data": {
        "rows": [{
          "layout": "one_by_one",
          "components": [{
            "title": "試験日の予定",
            "items": ["統計学の試験", "レポート提出", "友達と夕食"]
          }]
        }]
      }
    }
  ]
}
"""


def test_parse_job_when_valid() -> None:
    # Given a complete upstream capture job
    # When the trust boundary parses it
    job = CaptureJob.model_validate_json(VALID_JOB)

    # Then the worker receives a typed immutable contract
    assert job.job_id == "20260822-jp-001"
    assert job.scenes[0].capture_target == "trace_components"
    assert job.scenes[0].trace_data.items[2] == "友達と夕食"


def test_parse_job_when_component_scene_omits_background() -> None:
    raw_job = VALID_JOB.replace(
        '      "background_image": "inputs/backgrounds/exam-week.jpg",\n',
        "",
    )

    job = CaptureJob.model_validate_json(raw_job)

    assert job.scenes[0].background_image is None


def test_parse_job_when_reference_date_is_supplied_then_fixture_date_is_typed() -> None:
    # Given a capture job with an explicit marketing reference date
    background_line = '      "background_image": "inputs/backgrounds/exam-week.jpg",\n'
    date_line = '      "reference_date": "2026-09-25T12:30:00Z",\n'
    raw_job = VALID_JOB.replace(background_line, background_line + date_line)

    # When the capture contract parses the scene
    job = CaptureJob.model_validate_json(raw_job)

    # Then the Appium launch arguments can use the requested date instead of a hardcoded one
    assert job.scenes[0].reference_date.isoformat() == "2026-09-25T12:30:00+00:00"


def test_parse_job_when_device_kind_is_explicit() -> None:
    # Given the upstream job explicitly targets an iOS Simulator
    raw_job = VALID_JOB

    # When the device boundary parses it
    job = CaptureJob.model_validate_json(raw_job)

    # Then downstream routing receives the device kind as typed data
    assert job.device.kind.value == "simulator"


def test_parse_job_when_background_escapes_workspace() -> None:
    # Given a capture job whose background path escapes its input directory
    raw_job = VALID_JOB.replace(
        "inputs/backgrounds/exam-week.jpg",
        "../private/exam-week.jpg",
    )

    # When the trust boundary parses it
    # Then it rejects the unsafe path
    with pytest.raises(ValidationError):
        _ = CaptureJob.model_validate_json(raw_job)


def test_parse_job_when_scene_ids_repeat() -> None:
    # Given a job with duplicate scene identifiers
    second_scene = """
    ,{
      "scene_id": "lockscreen-01",
      "locale": "ko-KR",
      "capture_target": "trace_components",
      "background_image": "inputs/backgrounds/work.jpg",
      "trace_data": {"rows": [{"layout": "one_by_one", "components": [{
        "title": "오늘 일정", "items": ["회의", "운동", "보고서 제출"]
      }]}]}
    }
    """
    raw_job = VALID_JOB.replace("\n  ]", f"{second_scene}\n  ]")

    # When the trust boundary parses it
    # Then it rejects the ambiguous scene mapping
    with pytest.raises(ValidationError):
        _ = CaptureJob.model_validate_json(raw_job)


def test_parse_manifest_when_session_id_is_claimed_rejects_non_native_binding() -> None:
    # Given a native manifest that fabricates a WebDriver session field
    raw_manifest = {
        "schema_version": "trace.component-export-manifest.v1",
        "request_sha256": "a" * 64,
        "export_nonce": "b" * 64,
        "bundle_id": "com.corca.Trace",
        "device_udid": "E1FB798D-79E6-4B25-A987-D298A4FD122A",
        "session_id": "webdriver-session",
        "role": "trace_components",
        "artifact_sha256": "c" * 64,
        "width": 20,
        "height": 20,
    }

    # When the manifest crosses the native export contract
    with pytest.raises(ValidationError):
        _ = ComponentExportManifest.model_validate(raw_manifest)

    # Then the contract does not accept an unverifiable session claim
