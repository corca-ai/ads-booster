from __future__ import annotations

# pyright: reportUnknownMemberType=false
from typing import TYPE_CHECKING

from PIL import Image

from ads_booster.composition.composite_worker import CompositeWorker
from ads_booster.contracts import ErrorCode, JobStatus, MarketingCompositeJob
from tests.contracts.test_composite_contracts import VALID_COMPOSITE_JOB

if TYPE_CHECKING:
    from pathlib import Path


def test_composite_worker_when_all_layers_exist(tmp_path: Path) -> None:
    # Given a valid job and three independent layer files
    job = MarketingCompositeJob.model_validate_json(VALID_COMPOSITE_JOB)
    iphone_ui_path = job.layers.iphone_ui
    assert iphone_ui_path is not None
    background = tmp_path / job.layers.background
    components = tmp_path / job.layers.trace_components
    iphone_ui = tmp_path / iphone_ui_path
    background.parent.mkdir(parents=True)
    components.parent.mkdir(parents=True)
    iphone_ui.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2), (20, 30, 40)).save(background)
    component_image = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
    component_image.putpixel((0, 0), (0, 255, 0, 255))
    component_image.save(components)
    ui_image = Image.new("RGB", (2, 2), (0, 0, 0))
    ui_image.putpixel((1, 1), (255, 255, 255))
    ui_image.save(iphone_ui)

    # When the composition worker runs the full job
    result = CompositeWorker().run(job=job, job_root=tmp_path)

    # Then it writes the final image and a completed result
    assert result.status is JobStatus.COMPLETED
    assert (tmp_path / job.output_image).is_file()


def test_composite_worker_when_component_layer_is_missing(tmp_path: Path) -> None:
    # Given a valid job without its Appium-produced Trace component layer
    job = MarketingCompositeJob.model_validate_json(VALID_COMPOSITE_JOB)
    iphone_ui_path = job.layers.iphone_ui
    assert iphone_ui_path is not None
    background = tmp_path / job.layers.background
    iphone_ui = tmp_path / iphone_ui_path
    background.parent.mkdir(parents=True)
    iphone_ui.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2), (20, 30, 40)).save(background)
    Image.new("RGB", (2, 2), (0, 0, 0)).save(iphone_ui)

    # When the worker validates its source layers
    result = CompositeWorker().run(job=job, job_root=tmp_path)

    # Then it fails closed and names the missing input class
    assert result.status is JobStatus.FAILED
    assert result.errors[0].code is ErrorCode.INPUT_ASSET_MISSING


def test_composite_worker_when_output_is_symlinked_fails_closed(tmp_path: Path) -> None:
    # Given valid layers and an output path that redirects outside the job root
    job = MarketingCompositeJob.model_validate_json(VALID_COMPOSITE_JOB)
    iphone_ui_path = job.layers.iphone_ui
    assert iphone_ui_path is not None
    background = tmp_path / job.layers.background
    components = tmp_path / job.layers.trace_components
    iphone_ui = tmp_path / iphone_ui_path
    background.parent.mkdir(parents=True)
    components.parent.mkdir(parents=True)
    iphone_ui.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2), (20, 30, 40)).save(background)
    component_image = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
    component_image.putpixel((0, 0), (0, 255, 0, 255))
    component_image.save(components)
    Image.new("RGB", (2, 2), (0, 0, 0)).save(iphone_ui)
    outside = tmp_path / "outside-final.png"
    output = tmp_path / job.output_image
    output.parent.mkdir(parents=True, exist_ok=True)
    output.symlink_to(outside)

    # When the composition worker validates and runs the job
    result = CompositeWorker().run(job=job, job_root=tmp_path)

    # Then it fails before any bytes can be written through the symlink
    assert result.status is JobStatus.FAILED
    assert result.errors[0].code is ErrorCode.COMPOSITION_FAILED
    assert not outside.exists()
