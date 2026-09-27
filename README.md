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

## Control API

| Method and path | Purpose |
|---|---|
| `GET /_localqpu/health` | Status, backends, blocked CONNECT count |
| `GET`/`PUT /_localqpu/scenario` | Read or replace the scenario |
| `POST /_localqpu/reset` | Reset jobs, scenario, and stats |
| `GET /_localqpu/jobs` | Summary of submitted jobs |

## Supported primitives

| Qiskit primitive | Program | Notes |
|---|---|---|
| `SamplerV2` (legacy) | `sampler` | Exact, including mid-circuit measurements and `if_test` |
| `executor_sampler.Sampler` | `executor` (schema v2.0) | Exact |
| `EstimatorV2` (legacy) | `estimator` | Exact expectation values when no precision is requested; with a precision, Aer adds Gaussian noise of that size |
| `executor_estimator.Estimator` | `executor` (schema v2.0) | Expectation values are computed by the client from sampled measurements |

All of them also work inside `Session(...)` and `Batch(...)`.

## Simulation size

Circuits arrive laid out on the full chip (e.g. 127 qubits), but what makes simulation expensive is entanglement, not width. localqpu therefore limits the number of **entangled qubits** — qubits touched by multi-qubit gates — with `--max-sim-qubits` (default 24), and simulates exactly with Aer's matrix-product-state method, which is cheap for qubits that only get single-qubit gates or measurements. Above the limit, `sampler` and `estimator` return a shape-correct stub result (`metadata["localqpu_stub"]`) and `executor` fails with guidance.

## Limitations

- IBM Quantum Platform only. Qiskit Functions are not supported yet.
- `Session` and `Batch` are supported (create, status, `close()`, `cancel()`, `from_id`), but session timeouts (`max_time`, interactive timeout) are not emulated.
- Noiseless by default; set `"noise": true` in the scenario to add the chip's noise model.
- Jobs are kept in memory only and are lost when the server restarts.
- A running simulation cannot be interrupted. `POST /_localqpu/reset` (used by the pytest fixtures between tests) discards its result and starts a fresh worker pool, so later jobs are not blocked, but the old computation keeps using CPU until it finishes.
- Failure reasons use localqpu codes: `9000` for localqpu-side failures (invalid input, simulation limits) and `9001` for user cancellation.
