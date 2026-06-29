"""Simplest possible Textual test — type this in terminal:
   .venv/Scripts/python.exe tui_simple_test.py
"""
from textual.app import App, ComposeResult
from textual.widgets import Static, Header, Footer

class TestApp(App):
    TITLE = "Simple Test"
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("HELLO WORLD - This should be VERY visible!")
        yield Static("If you can read this, Textual is working.")
        yield Static("Type Ctrl+C or Ctrl+Q to exit.")
        yield Footer()

if __name__ == "__main__":
    TestApp().run()
