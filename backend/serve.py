import argparse
import os

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Start the local Listing workbench.")
    parser.add_argument("--host", default=os.getenv("LISTING_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--strictPort", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    uvicorn.run("backend.main:app", host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
