"""Prints a small report (a tool run by hand)."""
import requests


def main():
    print(requests.get("http://127.0.0.1/").status_code)


if __name__ == "__main__":
    main()
