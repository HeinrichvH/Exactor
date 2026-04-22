from abc import ABC, abstractmethod


class MemoryBackend(ABC):
    @abstractmethod
    def store(self, key: str, value: str) -> None: ...

    @abstractmethod
    def recall(self, query: str) -> str: ...

    @abstractmethod
    def flush(self) -> None: ...
