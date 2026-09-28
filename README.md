# localqpu

A local emulator of the IBM Quantum Platform API, built for testing.
Like a payment provider's "test mode", it lets you test code that calls a quantum cloud **without queues, without cost, and with any failure you want to simulate**.

> localqpu does not verify that quantum results are *correct*. It verifies **the code around the quantum API call**: submission, waiting, retries, result parsing, and error handling.

## Install and run

```bash
pip install localqpu      # https://pypi.org/project/localqpu/
localqpu start            # http://127.0.0.1:8787
```

### Docker

Multi-arch images (amd64, arm64) are published to GHCR for every release from v0.2.0 on:

```bash
docker run -p 127.0.0.1:8787:8787 ghcr.io/ictechgy/localqpu:latest
```

The image runs as an unprivileged user and has a `HEALTHCHECK` on `/_localqpu/health`, so it works as a CI service container — handy when the code under test is not Python:

```yaml
# GitHub Actions
services:
  localqpu:
    image: ghcr.io/ictechgy/localqpu:latest
    ports: ["8787:8787"]
```

## Connect existing Qiskit code

```python
import localqpu

service = localqpu.connect(port=8787)  # use this instead of QiskitRuntimeService(...)
backend = service.least_busy()
```

> ⚠️ **Always use `localqpu.connect()`.** In qiskit-ibm-runtime the runtime authentication endpoint is hard-coded to `iam.cloud.ibm.com` and can only be changed through the `IAM_URL` environment variable. If you configure the client by hand and miss it, the client **sends your key to the real IBM Cloud**. `connect()` sets `IAM_URL` and routes both HTTP and HTTPS through localqpu, so any leaking HTTPS request is blocked. Blocked attempts are counted in `blocked_connect_requests` on `GET /_localqpu/health`.
>
> Note that `connect()` changes process-wide environment variables: it sets `IAM_URL` and appends `localqpu.test` to `NO_PROXY`/`no_proxy` (so that `HTTP_PROXY` settings cannot redirect localqpu traffic to a corporate proxy). Run tests that talk to the real IBM Cloud in a separate process.

## AWS Braket (experimental)

Install the extra (Python 3.11+, because the Braket packages require it) and point the Amazon Braket SDK at localqpu:

```bash
pip install 'localqpu[braket]'
```

```python
import localqpu
from braket.aws import AwsDevice
from braket.circuits import Circuit

session = localqpu.connect_braket(port=8787)
device = AwsDevice("arn:aws:braket:::device/quantum-simulator/amazon/sv1", aws_session=session)
counts = device.run(Circuit().h(0).cnot(0, 1), shots=100).result().measurement_counts
```

- Emulates the SV1 simulator only, for gate-model OpenQASM tasks: create/get/cancel quantum tasks, get/search devices, and the S3 `results.json` download. Tasks share the job manager with IBM jobs, so failure scenarios (`next_jobs`, `failures`, `queue`) apply to them too.
- ⚠️ `connect_braket()` changes process-wide environment variables: it points `AWS_ENDPOINT_URL` (and the S3/Braket/STS-specific variants) at localqpu. The Braket SDK copies sessions internally and the copies lose explicitly passed clients — without this, result downloads went to the real AWS S3. With it, any AWS call the SDK makes lands on localqpu (unknown ones fail with 501) instead of leaking.
- Qubits are limited by `--max-sim-qubits` (all declared qubits count, since entanglement is not analysed for Braket programs). There is no stub mode and no seed control for Braket tasks.

## Use with pytest

The fixtures are registered automatically once the package is installed.

```python
def test_retry_on_failure(localqpu_service, localqpu_control):
    localqpu_control.set_scenario({"next_jobs": [{"outcome": "failed", "reason": "calibrating"}]})
    ...
```

| Fixture | Description |
|---|---|
| `localqpu_server` | A server on a free port for the whole test session |
| `localqpu_control` | Change scenarios, inspect jobs, reset state. Reset before and after each test |
| `localqpu_service` | A connected `QiskitRuntimeService` |

## Failure scenarios

Use `localqpu start --scenario scenario.json` or `localqpu_control.set_scenario({...})`.

```json
{
  "seed": 42,
  "queue": { "delay_seconds": 0, "polls_before_running": 1 },
  "failures": { "rate": 0.0, "reason": "Simulated failure", "reason_code": 9999 },
  "next_jobs": [
    { "outcome": "failed", "reason": "QPU calibration in progress", "reason_code": 1517 },
    { "outcome": "cancelled" }
  ],
  "backends": { "ibm_brisbane": { "status": "offline", "queue_length": 120 } },
  "usage": { "limit_seconds": 600, "consumed_seconds": 600 },
  "auth": { "reject_tokens": false },
  "noise": false
}
```

| Scenario | What the client sees |
|---|---|
| `next_jobs` failure | `RuntimeJobFailureError` (with the reason) |
| `next_jobs` cancellation | `RuntimeInvalidStateError` |
| Failure **or cancellation** with `reason_code: 1305` | `RuntimeJobMaxTimeoutError` (the client treats 1305 as a max-time error and turns a cancelled job with this code into an error) |
| `job.cancel()` from your code | `CANCELLED`; localqpu records `reason_code: 9001` |
| Backend `offline` | Excluded from `least_busy()`; submitted jobs stay `QUEUED` until it is back online |
| Backend `paused` | A "currently has a status of paused" warning, then normal processing |
| Usage limit reached | A warning, then submission fails with `IBMRuntimeError` (403) |
| `auth.reject_tokens` | `InvalidAccountError` when creating the service |
| `noise: true` | Results include the chip's gate and readout errors (noise model built from the same calibration snapshot, e.g. ~6% `01`/`10` on a Bell pair on `ibm_brisbane`) |

> `QiskitRuntimeService` caches the backend list after the first `least_busy()`/`backends()` call, exactly as it does against IBM. After changing backend status in a scenario, create a new service (the `localqpu_service` fixture gives you a fresh one per test).

## HTTP-level faults

Job-level scenarios cover what happens *after* the platform accepts a job. To test the transport layer — retries, rate limits, slow or broken connections — add `http_faults`. Rules are matched in order (`path` is an fnmatch pattern, `method` is optional) and each applies `times` requests (default 1):

```python
localqpu_control.set_scenario(
    {
        "http_faults": [
            {
                "method": "POST",
                "path": "/api/v1/jobs",
                "status": 503,
                "times": 2,
            },  # transient outage
            {
                "method": "POST",
                "path": "/api/v1/jobs",
                "status": 429,
                "retry_after": 30,
            },  # rate limit
            {"path": "/api/v1/jobs/*", "delay_seconds": 2},  # slow responses
            {
                "method": "GET",
                "path": "/api/v1/jobs/*",
                "drop_connection": True,
            },  # broken connection
            {
                "method": "POST",
                "path": "/api/v1/jobs",
                "status": 503,
                "phase": "after",
            },  # processed, response lost
        ]
    }
)
```

`phase: "after"` processes the request normally and only fails the response — the classic lost-response case. With qiskit-ibm-runtime 0.50, which retries 500/502/503/504/52x (including `POST`) up to 5 times but never 429, a lost `POST /api/v1/jobs` response makes the client **submit the job twice**. Control API paths are never faulted.

Assert on what actually went over the wire with the request journal (method, host, path, status, fault, duration — no bodies, headers or query strings):

```python
submissions = [r["status"] for r in localqpu_control.requests() if r["path"] == "/api/v1/jobs"]
assert submissions == [503, 503, 200]
```

## Control API

| Method and path | Purpose |
|---|---|
| `GET /_localqpu/health` | Status, backends, blocked CONNECT count |
| `GET`/`PUT /_localqpu/scenario` | Read or replace the scenario |
| `POST /_localqpu/reset` | Reset jobs, scenario, and stats |
| `GET /_localqpu/jobs` | Summary of submitted jobs |
| `GET /_localqpu/requests` | Recent request journal (last 1000, control API excluded) |

## Supported primitives

| Qiskit primitive | Program | Notes |
|---|---|---|
| `SamplerV2` (legacy) | `sampler` | Exact, including mid-circuit measurements and `if_test` |
| `executor_sampler.Sampler` | `executor` (schema v2.0) | Exact |
| `EstimatorV2` (legacy) | `estimator` | Exact expectation values when no precision is requested; with a precision, Aer adds Gaussian noise of that size |
| `executor_estimator.Estimator` | `executor` (schema v2.0) | Expectation values are computed by the client from sampled measurements |

All of them also work inside `Session(...)` and `Batch(...)`.

Job management works too: `service.job(id)`, `service.jobs(...)` (filters by backend, program, tags, session, pending, creation time, with paging), `job.metrics()`, `job.usage()` (always 0 — it is a simulator), `job.logs()`, `job.update_tags(...)` and `service.delete_job(id)`. Job inputs are not retained after a job starts running, so `job.inputs` is empty.

## Simulation size

Circuits arrive laid out on the full chip (e.g. 127 qubits), but what makes simulation expensive is entanglement, not width. localqpu therefore limits the number of **entangled qubits** — qubits touched by multi-qubit gates — with `--max-sim-qubits` (default 24), and simulates exactly with Aer's matrix-product-state method, which is cheap for qubits that only get single-qubit gates or measurements. Above the limit you get a stub instead of an error: `sampler` and `estimator` return shape-correct random values (`metadata["localqpu_stub"]`), and `executor` runs an approximate simulation with a capped MPS bond dimension, so the result structure is exact but the values are not meaningful. Stub jobs are flagged with `is_stub: true` in `GET /_localqpu/jobs` and logged by the server.

## Limitations

- IBM Quantum Platform, plus AWS Braket SV1 as an experimental extra. Qiskit Functions are not supported yet.
- `Session` and `Batch` are supported (create, status, `close()`, `cancel()`, `from_id`), but session timeouts (`max_time`, interactive timeout) are not emulated.
- Noiseless by default; set `"noise": true` in the scenario to add the chip's noise model.
- Jobs are kept in memory only and are lost when the server restarts.
- A running simulation cannot be interrupted. `POST /_localqpu/reset` (used by the pytest fixtures between tests) discards its result and starts a fresh worker pool, so later jobs are not blocked, but the old computation keeps using CPU until it finishes.
- Failure reasons use localqpu codes: `9000` for localqpu-side failures (invalid input, simulation limits) and `9001` for user cancellation.
