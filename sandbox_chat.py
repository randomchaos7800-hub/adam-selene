#!/usr/bin/env python3
"""Minimal CLI test harness — stdin/stdout, no external services."""

import sys
import logging

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from relay.relay import RelayV3

relay = RelayV3()

print(f"Selene sandbox — type a message, Ctrl-C to quit\n{'─'*50}")

while True:
    try:
        user_input = input("\nYou: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nbye")
        break

    if not user_input:
        continue

    try:
        response = relay.respond(user_input, user_id="dino", interface="cli")
        print(f"\nSelene: {response}")
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
