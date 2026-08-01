from __future__ import annotations

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
        maximum_score: float,
    ) -> None:
        self.telegram = telegram
        self.store = store
        self.authorized_chat_id = str(authorized_chat_id)
        self.maximum_score = maximum_score

    def poll_once(self, timeout_seconds: int = 2) -> int:
        offset = self.store.get_telegram_update_offset()
        updates = self.telegram.get_updates(offset=offset, timeout_seconds=timeout_seconds)
        handled = 0
        for update in updates:
            update_id = update.get("update_id")
            try:
                message = update.get("message", {})
                chat_id = str(message.get("chat", {}).get("id", ""))
                text = message.get("text", "")
                if chat_id != self.authorized_chat_id or not text.startswith("/"):
                    continue
                response = self.handle_command(text)
                self.telegram.send_message(response)
                handled += 1
            finally:
                if isinstance(update_id, int):
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
            return f"Score threshold: <b>{self.store.get_threshold():g}</b>"
        if command == "/clear_history":
            deleted = self.store.clear_history()
            return f"Notification history cleared: <b>{deleted}</b> records deleted."
        if command == "/set_threshold":
            try:
                threshold = float(argument)
            except ValueError:
                return f"Usage: {escape(first)} &lt;0-{self.maximum_score:g}&gt;"
            if not 0 <= threshold <= self.maximum_score:
                return f"Threshold must be between 0 and {self.maximum_score:g}."
            self.store.set_threshold(threshold)
            return f"Score threshold set to <b>{threshold:g}</b>."
        if command in {"/start", "/help"}:
            return (
                "<b>Filter commands</b>\n"
                "/show_skills\n/add_skill &lt;skill&gt;\n/delete_skill &lt;skill&gt;\n\n"
                "/show_search_words\n/add_search_word &lt;word&gt;\n"
                "/delete_search_word &lt;word&gt;\n\n"
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
