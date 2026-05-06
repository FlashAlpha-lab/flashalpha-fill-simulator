# Contributing

Thanks for helping make `flashalpha-fill-simulator` more reusable and honest.

## Development setup

```bash
python -m pip install --upgrade pip
pip install -e ".[test,dev]"
```

## Quality gates

Run these before opening a PR:

```bash
ruff check src tests
ruff format --check src tests
mypy src
pytest --cov=fillsim --cov-report=term-missing --cov-fail-under=85
```

Use `ruff format src tests` to apply formatting.

## Behavior changes

The simulator is a behavioral contract more than a pile of code. If a PR changes fill, exit, settlement, provider, or config semantics:

1. Update `docs/SPEC.md`.
2. Add or update tests using synthetic quotes.
3. Explain whether the change is backward-compatible.
4. Add a `CHANGELOG.md` entry.

## Provider adapters

New providers should implement the `ChainProvider` protocol, avoid runtime dependencies unless they are optional extras, and include small deterministic tests. Keep provider-specific caching and retries inside the provider so the core simulator stays pure.

## Commits

Please sign commits with DCO:

```bash
git commit -s
```
