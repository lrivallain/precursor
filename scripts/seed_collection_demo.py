from __future__ import annotations

import asyncio

from precursor.backend.services.demo_data import seed_collection_scope_demo

if __name__ == "__main__":
    result = asyncio.run(seed_collection_scope_demo())
    print(result)
