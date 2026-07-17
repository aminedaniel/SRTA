# SMCT Research

A systematic research platform for discovering underfollowed and potentially mispriced small- and mid-cap technology companies over a 24–36 month investment horizon.

## Current status

The initial implementation includes:

- canonical company, feature, signal, and thesis models
- plugin-based signal registry
- small/mid-cap technology universe filter
- valuation-compression signal
- Reddit awareness/crowding signal
- congressional disclosed-purchasing signal
- composite research-priority scorer
- CLI and tests

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
smct evaluate examples/sample_snapshot.json
```

See `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, and `docs/CONGRESSIONAL_DISCLOSURES.md` for the design and implementation sequence.
