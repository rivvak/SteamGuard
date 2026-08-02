# loader_ai/

PyQt5 chat modal for the SteamGuard loader. Launched from a "?" button
placed alongside the existing product cards in `loader.py`.

> The package is `loader_ai` (top-level), not `loader.ai`. A `loader/`
> package would be shadowed by the `loader.py` entry-point module that lives
> in the same directory — Python resolves `loader` to the `.py` file before the
> package directory, so `from loader.ai import ...` raises
> `"'loader' is not a package"`.

## Wire-up

In `loader.py`, near where product-card buttons are created:

```python
from loader_ai.chat_panel import open_chat_modal

def _open_ai_chat(self):
    session = self.session         # existing session object with .key + .hwid
    server_url = auth.client._SERVER_URL
    open_chat_modal(self, session.key, session.hwid, server_url)

# In your toolbar/menu/product-row layout:
self.ai_button = QPushButton("?")
self.ai_button.setToolTip("Ask the SteamGuard Assistant")
self.ai_button.clicked.connect(self._open_ai_chat)
# gate on server version so old servers don't show the button
if features.ai_ask_enabled():
    toolbar.addWidget(self.ai_button)
```

`features.ai_ask_enabled()` should probe `GET /health` and check for a
`features.ai_ask_enabled` flag returned by the server (added in Phase 1 as
a one-line addition to the existing health handler).

## Additional loader dependency

`requirements.txt` needs:

```
PyQtWebEngine
```

Already-present PyQt5 covers the rest.
