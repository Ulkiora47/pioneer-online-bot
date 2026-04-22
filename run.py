#!/usr/bin/env python3
"""Точка входа — загружает .env и запускает бота."""
from dotenv import load_dotenv
load_dotenv()
from bot import main
if __name__ == "__main__":
    main()
