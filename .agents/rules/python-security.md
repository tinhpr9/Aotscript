---
paths:
  - "**/*.py"
  - "**/*.pyi"
---
# Python Security

> This file extends `common-security.md` with Python specific content.

## Secret Management

```python
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.environ["API_KEY"]  # Raises KeyError if missing
```

## Security Scanning

- Use **bandit** for static security analysis:
  ```bash
  bandit -r aot-group-control/
  ```

## Reference

See `rules/common-security.md` for project security guidelines.
