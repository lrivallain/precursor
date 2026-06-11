"""``python -m precursor.backend`` — convenience launcher."""

from __future__ import annotations

import uvicorn

from precursor.backend.config import get_settings


def main() -> None:
    cfg = get_settings()
    uvicorn.run(
        "precursor.backend.main:app",
        host=cfg.host,
        port=cfg.port,
        log_level=cfg.log_level,
        reload=False,
    )


if __name__ == "__main__":
    main()
