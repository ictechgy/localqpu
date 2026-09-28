"""실제 클라이언트의 작업 조회·관리 API(service.jobs, job.metrics/logs/update_tags, delete_job)를 검증한다."""

import pytest
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2
from qiskit_ibm_runtime.exceptions import RuntimeJobNotFound

from localqpu.control_client import LocalqpuControl
from tests.contract.helpers import bell_isa_circuit

pytestmark = pytest.mark.contract


def run_tagged(service: QiskitRuntimeService, tags: list[str]) -> object:
    """태그를 붙여 Bell 작업을 제출하고 끝날 때까지 기다린 작업."""
    backend = service.backend("ibm_brisbane")
    sampler = SamplerV2(mode=backend)
    sampler.options.environment.job_tags = tags
    job = sampler.run([bell_isa_circuit(backend)], shots=10)
    job.result(timeout=120)
    return job


def test_jobs_listing_with_filters(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """service.jobs()가 태그·백엔드 필터와 페이지 나눔(limit보다 많은 작업)으로 작업을 되찾는다."""
    tagged = [run_tagged(localqpu_service, ["sweep"]).job_id() for _ in range(3)]
    run_tagged(localqpu_service, ["other"])
    found = localqpu_service.jobs(job_tags=["sweep"], backend_name="ibm_brisbane", limit=None)
    assert sorted(job.job_id() for job in found) == sorted(tagged)
    assert len(localqpu_service.jobs(limit=2)) == 2
    assert {job.job_id() for job in localqpu_service.jobs(pending=True)} == set()


def test_metrics_usage_and_logs(localqpu_service: QiskitRuntimeService) -> None:
    """끝난 작업의 metrics·usage·logs를 읽는다(시뮬레이터라 QPU 사용 시간은 0)."""
    job = run_tagged(localqpu_service, [])
    assert job.metrics()["timestamps"]["running"]
    assert job.usage() == 0
    assert job.job_id() in job.logs()


def test_update_tags_and_retrieve(localqpu_service: QiskitRuntimeService) -> None:
    """태그를 바꾸면 다시 조회한 작업에도 반영된다."""
    job = run_tagged(localqpu_service, ["draft"])
    assert localqpu_service.job(job.job_id()).tags == ["draft"]
    assert sorted(job.update_tags(["final", "paper"])) == ["final", "paper"]
    assert sorted(localqpu_service.job(job.job_id()).tags) == ["final", "paper"]


def test_delete_job(localqpu_service: QiskitRuntimeService) -> None:
    """삭제한 작업은 다시 조회할 수 없다."""
    job = run_tagged(localqpu_service, [])
    localqpu_service.delete_job(job.job_id())
    with pytest.raises(RuntimeJobNotFound):
        localqpu_service.job(job.job_id())
