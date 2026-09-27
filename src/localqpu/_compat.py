"""qiskit-ibm-runtime 내부 모듈 의존을 한곳에 모은다.

localqpu는 클라이언트와 똑같이 인코딩·디코딩하려고 공개 API가 아닌 내부 모듈을 쓴다.
SDK가 바뀌면 이 파일만 고치면 되도록, 다른 모듈은 이 심볼들을 여기서만 가져온다(설계서 9절).
"""

from __future__ import annotations

from qiskit_ibm_runtime.json import RuntimeDecoder, RuntimeEncoder

__all__ = ["RuntimeDecoder", "RuntimeEncoder"]
