import json
import os
from urllib.request import Request, urlopen

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    ollama_url = os.environ["OLLAMA_URL"].rstrip("/")
    model = os.environ["MODEL"]
    request_body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        }
    ).encode("utf-8")
    request = Request(
        f"{ollama_url}/api/chat",
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=120) as response:
        result = json.load(response)
    print(result["message"]["content"])


if __name__ == "__main__":
    main()
