"""ONE-CLICK LAUNCHER. Starts the local web app and opens your browser.

Double-click run_app.bat, or run:  python run_app.py
Leave it running = the app is "always on" at http://127.0.0.1:8000. Ctrl+C stops it.
"""
import threading
import webbrowser

import uvicorn

URL = "http://127.0.0.1:8000"


def _open_browser() -> None:
    webbrowser.open(URL)


if __name__ == "__main__":
    # Open the browser shortly after the server starts accepting connections.
    threading.Timer(1.5, _open_browser).start()
    print(f"Job Application Pipeline running at {URL}   (Ctrl+C to stop)")
    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=False)
