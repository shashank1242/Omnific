"""SimResult: what generated solver code must return. The Sandbox runner serialises it; evaluators and exporters read it."""

import numpy as np
from pydantic import BaseModel, ConfigDict


class SimResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    t: np.ndarray
    """Sample times, shape (n,), in the SimSpec.time unit."""

    state: dict[str, np.ndarray]
    """State variable name -> values, shape (n,) for scalars or (n, ...) for fields."""

    units: dict[str, str]
    """State variable name -> pint unit string."""

    meta: dict[str, float | str] = {}
    """Solver diagnostics, e.g. {'solver': 'RK45', 'nfev': 1234}."""

    def save_npz(self, path: str) -> None:
        np.savez_compressed(
            path, t=self.t, **{f"state__{k}": v for k, v in self.state.items()},
            units=np.array([f"{k}={v}" for k, v in self.units.items()]),
        )

    @classmethod
    def load_npz(cls, path: str) -> "SimResult":
        data = np.load(path, allow_pickle=False)
        units = dict(s.split("=", 1) for s in data["units"].tolist())
        state = {k.removeprefix("state__"): data[k] for k in data.files if k.startswith("state__")}
        return cls(t=data["t"], state=state, units=units)
