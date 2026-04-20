---
title: Public eval suite split
status: active
---

# Public eval suite split

We keep the public eval suite synthetic and self-contained so it does not depend on the private corpus.
The public validator defaults to `eval/public/questions` and `examples/corpus`, while the private corpus stays opt-in.

This decision keeps the public docs safe to ship and still lets us validate the real corpus when needed.
