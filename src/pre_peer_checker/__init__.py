"""論文投稿前データ照合AI — コアパッケージ."""

import os

# torch registers its Triton native ops at import time, so this must be set
# before anything imports torch; setting it inside the LLM/VLM load() is too
# late and every CUDA generate() then fails when Python headers are missing.
os.environ.setdefault("TORCH_DISABLE_NATIVE_JIT", "1")

__version__ = "0.1.0"
