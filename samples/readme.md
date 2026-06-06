# Project Title

A brief description of what this project does and who it's for.

## Features

- **Lossless compression** — every byte preserved
- _Universal tokenizer_ — works across all v1 formats
- `LMDB-backed` dictionary for production speed

## Installation

```bash
pip install eloai
```

## Usage

```python
from eloai import compress, decompress

stream = compress('hello.txt')
recovered = decompress(stream)
```

## License

MIT © 2026
