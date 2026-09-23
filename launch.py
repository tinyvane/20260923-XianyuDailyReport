"""Start the local dashboard in the browser selected by the user."""
import subprocess
import threading
import webbrowser
from pathlib import Path

import uvicorn
import monitor


def open_dashboard():
    monitor.init_db()
    mode = monitor.settings()["browser_mode"]
    if mode == "edge":
        webbrowser.open("http://127.0.0.1:5055/")
        return
    if mode == "edge_extension":
        paths = [
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        ]
        browser = next((path for path in paths if path.exists()), None)
        if browser:
            subprocess.Popen([str(browser), "--new-tab", "http://127.0.0.1:5055/"])
        else:
            print("未找到 Edge。请在已登录的 Edge 中手动打开 http://127.0.0.1:5055/")
        return
    paths = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    chrome = next((path for path in paths if path.exists()), None)
    if chrome:
        subprocess.Popen([str(chrome), "--new-tab", "http://127.0.0.1:5055/"])
    else:
        print("未找到 Chrome。请在已登录的 Chrome 中手动打开 http://127.0.0.1:5055/")


if __name__ == "__main__":
    threading.Timer(1.5, open_dashboard).start()
    uvicorn.run("monitor:app", host="127.0.0.1", port=5055, log_level="info")
