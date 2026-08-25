import pytest
from pydantic import ValidationError

from trace_capture.contracts import MarketingCompositeJob

VALID_COMPOSITE_JOB = """
{
  "schema_version": "trace.marketing-composite-job.v2",
  "job_id": "jp-lockscreen-001",
  "context": {
    "country": "JP",
    "persona_id": "jp-office-worker",
    "promotion_material_id": "monthly-calendar"
  },
  "canvas": {"width": 942, "height": 2048},
  "layers": {
    "background": "inputs/background.jpg",
    "trace_components": "work/trace-components.png",
    "iphone_ui": "inputs/iphone-ui.png"
  },
  "output_image": "outputs/final.png"
}
"""


def test_parse_composite_job_when_layers_are_distinct() -> None:
    # Given a versioned job with three independent image layers
    # When the compositor boundary parses it
    job = MarketingCompositeJob.model_validate_json(VALID_COMPOSITE_JOB)

    # Then each layer remains independently addressable
    assert job.canvas.width == 942
    assert job.layers.background == "inputs/background.jpg"
    assert job.layers.trace_components == "work/trace-components.png"
    assert job.layers.iphone_ui == "inputs/iphone-ui.png"


def test_parse_composite_job_when_layer_paths_collide() -> None:
    # Given a job that reuses the background as its Trace component layer
    raw_job = VALID_COMPOSITE_JOB.replace(
        '"work/trace-components.png"',
        '"inputs/background.jpg"',
    )

    # When the compositor boundary parses it
    # Then the job is rejected before any image work starts
    with pytest.raises(ValidationError):
        _ = MarketingCompositeJob.model_validate_json(raw_job)
