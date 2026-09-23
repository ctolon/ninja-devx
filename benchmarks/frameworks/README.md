# Cross-framework workload characterization

One `GET /items` endpoint per framework over the same payload, measured in-process through
Django's test client. This characterizes the abstraction each framework adds; it is not a
ranking, a latency promise, or a CI gate. Compare ratios from the same run and machine.

```bash
python benchmarks/frameworks/overhead.py
python benchmarks/frameworks/overhead.py --requests 5000 --rounds 7
python benchmarks/frameworks/overhead.py --framework ninja --framework ninja-devx
```

Django Ninja and ninja-devx adapters are always available. Django REST framework and
django-ninja-extra adapters appear only when importable; run them without changing the
project lockfile:

```bash
uv run --isolated --with djangorestframework python benchmarks/frameworks/overhead.py
uv run --isolated --with django-ninja-extra python benchmarks/frameworks/overhead.py
```

django-ninja-crud is a model-backed viewset and is intentionally not part of this
HTTP-only workload; measure it in an application with a database.

Each round issues `--requests` requests after a warmup; the fastest round is reported.
The database and query behavior are covered by `benchmarks/overhead.py`,
`benchmarks/load.py` and `benchmarks/data_paths.py` instead.
