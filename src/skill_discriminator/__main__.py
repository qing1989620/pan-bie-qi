"""Run the local Skill assessment demo."""

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8853)
    args = parser.parse_args()
    uvicorn.run("skill_discriminator.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
