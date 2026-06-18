from dataclasses import dataclass

@dataclass
class SingleResult:
    pins: list[int]
    y_location: float
    capture_time: str