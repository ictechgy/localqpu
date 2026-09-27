# localqpu

A local emulator of the IBM Quantum Platform API, built for testing.
Like a payment provider's "test mode", it lets you test code that calls a quantum cloud **without queues, without cost, and with any failure you want to simulate**.

> localqpu does not verify that quantum results are *correct*. It verifies **the code around the quantum API call**: submission, waiting, retries, result parsing, and error handling.

## Install and run

```bash
pip install localqpu
localqpu start            # http://127.0.0.1:8787
# or
docker build -t localqpu . && docker run -p 127.0.0.1:8787:8787 localqpu
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
  "auth": { "reject_tokens": false }
}
```

| Scenario | What the client sees |
|---|---|
| `next_jobs` failure | `RuntimeJobFailureError` (with the reason) |
| `next_jobs` cancellation | `RuntimeInvalidStateError` |
| Failure with `reason_code: 1305` | `RuntimeJobMaxTimeoutError` (the client treats 1305 as a max-time error) |
| Backend `offline` | Excluded from `least_busy()`; submitted jobs stay `QUEUED` until it is back online |
| Backend `paused` | A "currently has a status of paused" warning, then normal processing |
| Usage limit reached | A warning, then submission fails with `IBMRuntimeError` (403) |
| `auth.reject_tokens` | `InvalidAccountError` when creating the service |

> `QiskitRuntimeService` caches the backend list after the first `least_busy()`/`backends()` call, exactly as it does against IBM. After changing backend status in a scenario, create a new service (the `localqpu_service` fixture gives you a fresh one per test).

## Control API

| Method and path | Purpose |
|---|---|
| `GET /_localqpu/health` | Status, backends, blocked CONNECT count |
| `GET`/`PUT /_localqpu/scenario` | Read or replace the scenario |
| `POST /_localqpu/reset` | Reset jobs, scenario, and stats |
| `GET /_localqpu/jobs` | Summary of submitted jobs |

## Limitations (v0.1)

- IBM Quantum Platform only. Supported programs: the legacy `SamplerV2` (`sampler`) and the new executor-based Sampler (`executor`, schema v2.0).
- Noiseless simulation. If the number of active qubits exceeds `--max-sim-qubits` (default 24), the sampler returns a shape-correct stub result (`metadata["localqpu_stub"]`) and the executor fails with guidance.
- Estimator, Session/Batch, and Qiskit Functions are not supported yet.
- Jobs are kept in memory only and are lost when the server restarts.
