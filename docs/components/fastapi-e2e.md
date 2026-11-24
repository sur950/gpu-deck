# End-to-end tests

`test_*_journey.py` files that log in for real (`POST /api/v1/auth/login`) and drive the admin API, the serving API and real engines on an Apple silicon Mac (llama.cpp router mode with `qwen3:0.6b`, MLX with `gemma-3-1b-it-qat-4bit`, nomic embeddings).

Name created resources `zz_e2e_<uuid>` and delete them in teardown. Run with:

```bash
./tests/scripts/run_tests.sh e2e
```
