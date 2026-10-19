from __future__ import annotations

import logging
import time
from dataclasses import fields
from html import escape

from .state import SeenProjectStore
from .telegram import TelegramClient

logger = logging.getLogger(__name__)

SCORE_COMMANDS = {
    "payment_verified": "Set payment verification points",
    "deposit_made": "Set deposit points",
    "profile_completed": "Set completed profile points",
    "identity_verified": "Set identity verification points",
    "phone_verified": "Set phone verification points",
    "email_verified": "Set email verification points",
    "rating_max": "Set maximum rating points",
    "reviews_max": "Set maximum review points",
    "reviews_cap": "Set review count for maximum points",
    "member_age_max": "Set maximum account age points",
    "member_age_max_days": "Set account age in days for maximum points",
}

BOT_COMMANDS = (
    ("show_skills", "Show monitored skills"),
    ("add_skill", "Add a monitored skill"),
    ("delete_skill", "Delete a monitored skill"),
    ("show_search_words", "Show search keywords"),
    ("add_search_word", "Add a search keyword"),
    ("delete_search_word", "Delete a search keyword"),
    ("show_score", "Show owner scoring settings"),
    *((f"set_{name}", description) for name, description in SCORE_COMMANDS.items()),
    ("show_threshold", "Show the minimum project score"),
    ("set_threshold", "Change the minimum project score"),
    ("clear_history", "Clear history; projects may notify again"),
    ("help", "Show commands and usage"),
    ("start", "Show getting-started commands"),
)


class TelegramCommandProcessor:
    def __init__(
        self,
        telegram: TelegramClient,
        store: SeenProjectStore,
        *,
        authorized_chat_id: str,
    ) -> None:
        self.telegram = telegram
        self.store = store
        self.authorized_chat_id = str(authorized_chat_id)
        self._menu_registered = False
        self._menu_retry_at = 0.0

    def poll_once(self, timeout_seconds: int = 2) -> int:
        if not self._menu_registered and time.monotonic() >= self._menu_retry_at:
            try:
                self.telegram.set_commands(BOT_COMMANDS)
            except Exception:
                # Menu setup must not stop command handling or notification workers.
                logger.warning("Telegram command menu registration failed; retrying in 60 seconds")
                self._menu_retry_at = time.monotonic() + 60
            else:
                self._menu_registered = True
        offset = self.store.get_telegram_update_offset()
        updates = self.telegram.get_updates(offset=offset, timeout_seconds=timeout_seconds)
        handled = 0
        for update in updates:
            update_id = update.get("update_id")
            if not isinstance(update_id, int):
                continue
            message = update.get("message", {})
            chat_id = str(message.get("chat", {}).get("id", ""))
            text = message.get("text", "")
            if chat_id == self.authorized_chat_id and isinstance(text, str) and text.startswith("/"):
                response = self.handle_command(text)
                self.store.enqueue_reply(response, update_id)
                handled += 1
            else:
                self.store.set_telegram_update_offset(update_id + 1)
        return handled

    def handle_command(self, text: str) -> str:
        first, separator, argument = text.strip().partition(" ")
        command = first.split("@", 1)[0].casefold()
        argument = argument.strip() if separator else ""

        term_commands = {
            "/add_skill": ("skill", "skill", "add"),
            "/delete_skill": ("skill", "skill", "delete"),
            "/add_search_word": ("search_word", "search word", "add"),
            "/delete_search_word": ("search_word", "search word", "delete"),
        }
        if command in term_commands:
            kind, label, action = term_commands[command]
            if not argument:
                return f"Usage: {escape(first)} &lt;{label}&gt;"
            if action == "add":
                changed = self.store.add_term(kind, argument)
                result = "Added" if changed else "Already exists"
            else:
                changed = self.store.delete_term(kind, argument)
                result = "Deleted" if changed else "Not found"
            return f"{result}: <b>{escape(argument)}</b>"

        if command == "/show_skills":
            return self._format_terms("Skills", self.store.list_terms("skill"))
        if command == "/show_search_words":
            return self._format_terms(
                "Search words", self.store.list_terms("search_word")
            )
        if command == "/show_threshold":
            config, threshold = self.store.get_scoring_settings()
            return f"Score threshold: <b>{threshold:g}</b> / {config.maximum:g} maximum"
        if command == "/show_score":
            config, threshold = self.store.get_scoring_settings()
            lines = ["<b>Owner scoring</b>", f"Threshold: <b>{threshold:g}</b> / {config.maximum:g} maximum"]
            lines.extend(
                f"<code>{field.name}</code>: {getattr(config, field.name):g} — /set_{field.name}"
                for field in fields(config)
            )
            lines.extend([
                "", "Weights are points; 0 disables a signal.",
                "reviews_cap: review count for maximum points.",
                "member_age_max_days: account age in days for maximum points.",
                "rating_max: maximum points at 5 stars (linear).",
                "", "Use a /set_ command followed by a value.", "/set_threshold &lt;score&gt;",
            ])
            if threshold > config.maximum:
                lines.append("Threshold exceeds maximum; use /set_threshold to enable alerts.")
            return "\n".join(lines)
        score_setting = command.removeprefix("/set_") if command.startswith("/set_") else None
        if command == "/set_score" or score_setting in SCORE_COMMANDS:
            parts = argument.split()
            if score_setting in SCORE_COMMANDS:
                if len(parts) != 1:
                    config, _ = self.store.get_scoring_settings()
                    return (
                        f"Usage: {escape(command)} &lt;value&gt;\n"
                        f"{SCORE_COMMANDS[score_setting]}. Current value: <b>{getattr(config, score_setting):g}</b>."
                    )
                name, value = score_setting, parts[0]
            else:
                # Keep previously documented commands working, but show individual setters in the menu.
                if len(parts) != 2:
                    return "Usage: /set_score &lt;setting&gt; &lt;value&gt;. Use /show_score for settings."
                name, value = parts
                name = name.casefold()
            try:
                config, threshold = self.store.set_score_setting(name, value)
            except ValueError as exc:
                return escape(str(exc))
            return (
                f"Set <code>{escape(name)}</code> to <b>{getattr(config, name):g}</b>.\n"
                f"Threshold: {threshold:g} / {config.maximum:g} maximum. Applies to the next poll."
            )
        if command == "/clear_history":
            deleted = self.store.clear_history()
            return f"Notification history cleared: <b>{deleted}</b> records deleted."
        if command == "/set_threshold":
            try:
                threshold = float(argument)
            except ValueError:
                return f"Usage: {escape(first)} &lt;score&gt;. Use /show_score for the current maximum."
            try:
                self.store.set_threshold(threshold)
            except ValueError as exc:
                return escape(str(exc))
            return f"Score threshold set to <b>{threshold:g}</b>."
        if command in {"/start", "/help"}:
            score_help = "\n".join(
                f"/set_{name} &lt;value&gt; — {description}"
                for name, description in SCORE_COMMANDS.items()
            )
            return (
                "<b>Filter commands</b>\n"
                "/show_skills\n/add_skill &lt;skill&gt;\n/delete_skill &lt;skill&gt;\n\n"
                "/show_search_words\n/add_search_word &lt;word&gt;\n"
                "/delete_search_word &lt;word&gt;\n\n"
                f"/show_score\n{score_help}\n"
                "/show_threshold\n/set_threshold &lt;score&gt;\n\n"
                "/clear_history"
            )
        return "Unknown command. Use /help to see available commands."

    @staticmethod
    def _format_terms(title: str, terms: tuple[str, ...]) -> str:
        if not terms:
            return f"<b>{title}</b>\nNone configured."
        return f"<b>{title}</b>\n" + "\n".join(
            f"• {escape(term)}" for term in terms
        )
