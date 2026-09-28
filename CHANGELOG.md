# Changelog

## 0.2.1 — 2026-09-28

### Fixed
- **Braket qubit limit bypass**: qubits were counted with a regular expression that missed `qubit [25] q;` (space before the bracket) and `qubit[n] q;` (constant size), so such programs skipped `--max-sim-qubits` and could exhaust memory. Qubits are now counted with Braket's own OpenQASM interpreter (the qubits the program actually uses, which is what the simulator allocates).
- **Braket `task.queue_position()`** raised `KeyError`: `GetQuantumTask` now includes `queueInfo`.
- **Braket `clientToken` idempotency**: creating a task twice with the same token now returns the same task, as the AWS API does.
- **Braket device status**: the scenario can set `sv1` offline (the server used to reject it at start); `GetDevice` reports `OFFLINE` and tasks stay `QUEUED`.
- **Session race**: a job submitted while its session was being closed could stay queued in the closed session; it is now cancelled and the submission gets 409.

## 0.2.0 — 2026-09-27

### Added
- **Estimator**: legacy `EstimatorV2` (`program_id="estimator"`, exact expectation values; Gaussian noise of the requested precision when one is given) and the new executor-based `Estimator`.
- **Session and Batch**: create, status, `close()`, `cancel()`, `Session.from_id`. Submitting to a closed session returns 409.
- **Noise**: `"noise": true` in the scenario applies the noise model built from the same chip snapshot.
- **AWS Braket (experimental)**: SV1 simulator emulation for gate-model OpenQASM tasks, installed with `pip install 'localqpu[braket]'` (Python 3.11+). Use `localqpu.connect_braket()`.
- **executor stub mode**: executor jobs above `--max-sim-qubits` run as a bond-dimension-capped MPS approximation instead of failing (exact result structure, approximate values, flagged with `is_stub`).
- **Docker images on GHCR**: `ghcr.io/ictechgy/localqpu` (amd64, arm64), non-root user, `HEALTHCHECK`.

### Changed
- **Breaking**: the simulation limit now counts *entangled* qubits (qubits touched by multi-qubit gates) and simulation uses Aer's matrix-product-state method, so full-chip circuits with few entangled qubits simulate exactly and fast. The control API job summary field `active_qubits` is renamed to **`entangled_qubits`**.
- Job summaries include `session_id`.

## 0.1.0 — 2026-09-27

- First release: IBM Quantum Platform API emulation for `SamplerV2` and the executor-based Sampler, failure scenarios, control API, `localqpu.connect()`, pytest plugin, CLI.
