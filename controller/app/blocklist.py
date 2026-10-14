"""People who may not use the Discord commands. Two sources:
 - DISCORD_BLOCKED_IDS in controller.env (fixed, edited by hand)
 - /jarvis block and /jarvis unblock (saved in state/blocked_users.json)"""
import json
import logging
import os
import re

log = logging.getLogger("blocklist")


class Blocklist:
    def __init__(self, cfg):
        self.path = os.path.join(cfg.state_dir, "blocked_users.json")
        self.fixed = {int(x) for x in re.split(r"[,\s]+", cfg.discord_blocked_ids) if x.strip().isdigit()}
        self.dynamic = self._load()

    def _load(self) -> set:
        try:
            with open(self.path) as f:
                return {int(x) for x in json.load(f)}
        except (OSError, ValueError, TypeError):
            return set()

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as f:
                json.dump(sorted(self.dynamic), f)
        except OSError:
            log.exception("could not save the block list")

    def is_blocked(self, user_id: int) -> bool:
        return user_id in self.fixed or user_id in self.dynamic

    def add(self, user_id: int) -> bool:
        """False if they were already blocked."""
        if self.is_blocked(user_id):
            return False
        self.dynamic.add(user_id)
        self._save()
        return True

    def remove(self, user_id: int) -> str:
        """'removed', 'not blocked', or 'in config' (blocked in controller.env, edit that file instead)."""
        if user_id in self.dynamic:
            self.dynamic.discard(user_id)
            self._save()
            return "removed"
        return "in config" if user_id in self.fixed else "not blocked"

    def all(self) -> list:
        return sorted(self.fixed | self.dynamic)
