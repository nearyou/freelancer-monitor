from __future__ import annotations

from dataclasses import fields
from html import escape

from .state import SeenProjectStore
from .telegram import TelegramClient


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

    def poll_once(self, timeout_seconds: int = 2) -> int:
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
            lines.extend(f"<code>{field.name}</code>: {getattr(config, field.name):g}" for field in fields(config))
            lines.extend([
                "", "Weights are points; 0 disables a signal.",
                "reviews_cap: review count for maximum points.",
                "member_age_max_days: account age in days for maximum points.",
                "rating_max: maximum points at 5 stars (linear).",
                "", "/set_score &lt;setting&gt; &lt;value&gt;", "/set_threshold &lt;score&gt;",
            ])
            if threshold > config.maximum:
                lines.append("Threshold exceeds maximum; use /set_threshold to enable alerts.")
            return "\n".join(lines)
        if command == "/set_score":
            parts = argument.split()
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
            return (
                "<b>Filter commands</b>\n"
                "/show_skills\n/add_skill &lt;skill&gt;\n/delete_skill &lt;skill&gt;\n\n"
                "/show_search_words\n/add_search_word &lt;word&gt;\n"
                "/delete_search_word &lt;word&gt;\n\n"
                "/show_score\n/set_score &lt;setting&gt; &lt;value&gt;\n"
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
