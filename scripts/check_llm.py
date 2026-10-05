"""Check the Gemini key and model:  python scripts/check_llm.py [model]"""

import json
import os
import sys
import urllib.request

from perfhound.llm.client import DEFAULT_MODEL, GeminiClient

key = os.environ.get("GEMINI_API_KEY")
if not key:
    sys.exit("GEMINI_API_KEY is not set in this terminal")
req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                             headers={"x-goog-api-key": key})
models = [m["name"].removeprefix("models/") for m in json.loads(urllib.request.urlopen(req, timeout=30).read())["models"]
          if "generateContent" in m.get("supportedGenerationMethods", [])]
print("flash models:", ", ".join(m for m in models if "flash" in m))
model = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
print(f"\ntesting {model} ...")
answer = GeminiClient(model).complete_json('Return JSON {"ok": true, "n": 3}.')
print("answer:", answer, "-> OK" if answer.get("ok") else "-> unexpected")
