# How to Install on Windows — for complete beginners

You do **not** need to know anything about programming, and you do **not** need to
type any commands. Everything below is clicking and waiting.

**What this app is:** a local web page where four pretend AI "modules" talk to
each other about a task you type in. It runs entirely on your own computer. It
works without an account, without an API key, and without an internet connection
once it is installed.

**What you need**

| | |
| --- | --- |
| Computer | Windows 10 or 11 |
| Internet | Only for the first run (it downloads some files, about 2–5 minutes) |
| Time | About 10 minutes, most of it waiting |
| Admin rights | Usually not needed |
| Money | Nothing to pay, nothing to sign up for |

---

## Step 1 — Install Python (skip if you already have it)

**Do I already have it?** Press the `Windows` key, type `cmd`, press `Enter`.
In the black window, type `py --version` and press `Enter`.

- If it prints something like `Python 3.12.4` → **you already have it. Go to Step 2.**
- If it says the command is not recognized, or opens the Microsoft Store → do the following.

**Installing Python**

1. Open <https://www.python.org/downloads/> in your browser.
2. Click the big yellow **Download Python 3.xx** button.
3. When the download finishes, click the file to open it (usually in your
   **Downloads** folder, named something like `python-3.12.4-amd64.exe`).
4. On the **first screen of the installer**, tick the box at the bottom that says
   **“Add python.exe to PATH”**. This is the one step people miss, and it is the
   one that breaks everything afterwards.
5. Click **Install Now** and wait for it to finish. Click **Close**.

That is all — you never have to open Python itself.

---

## Step 2 — Download this project and unzip it

1. On the project's GitHub page, click the green **Code** button.
2. Click **Download ZIP**.
3. When the download finishes, find the `.zip` file (usually in your
   **Downloads** folder).
4. **Right-click it → Extract All… → Extract.**
5. Open the extracted folder. You should see a file named
   **`launch-windows.bat`** in it.

> **Important:** the app must be run from the extracted folder, not from inside the
> zip file. If you can only see the zip, you have not extracted it yet.
>
> **Where should I put it?** Anywhere you like — your Desktop is fine. A folder
> whose path contains spaces (for example `C:\Users\Anna\My Downloads\...`) is
> also fine.

---

## Step 3 — Start it

1. **Double-click `launch-windows.bat`.**
2. A black window opens and prints what it is doing. On the very first run it
   says it is downloading the parts it needs — this takes a few minutes.
   **Do not close the window.**
3. When it is ready it prints a line like
   `Starting the dashboard at http://127.0.0.1:8000` and your browser opens
   automatically.

If Windows shows a blue box saying **“Windows protected your PC”**: click
**More info** → **Run anyway**. This appears because the file came from the
internet, not because anything is wrong.

Keep the black window open the whole time you use the app — it *is* the app.

---

## Step 4 — Use it (the first 30 seconds)

In the browser page that opened:

1. **Quick Scenarios** (top of the middle panel): click one of the buttons, for
   example **“Arena AI ⇄ Copilot (Rate Limiter)”**. It fills in the task for you.
2. Click the purple **Execute Dialogue** button.
3. Watch the four modules answer one after another in the panel below.

No API keys are needed. With no keys configured every module answers from a
built-in simulator, and each of its messages is labelled **simulated** — that
label is honest: no real AI model was called. If you want real answers from
OpenAI or Claude later, click **Settings / Keys** and paste your key; it is kept
in memory for that browser session only and is never written to disk.

**Useful things to try**

| I want to… | Do this |
| --- | --- |
| Ask my own question | Type it in the **Task Specification / Prompt** box, then click **Execute Dialogue** (or press `Ctrl`+`Enter`) |
| Hear the conversation | Click **Read All**, or a 🔊 button on a single message |
| Keep the conversation | Click **Sessions** → type a name → **Save** |
| Get the text out | Click **Export MD** or **Export JSON** |
| Add my own module | Click **Add Module** (for example a "Security Reviewer" with its own role) |
| Stop a long run | Click **Stop** (it stops after the current module finishes) |
| Stop the whole app | Close the black window, or click in it and press `Ctrl`+`C` |

---

## Step 5 — Start it again another day

Double-click `launch-windows.bat` again. The first run installed everything, so
this time it starts in a couple of seconds and goes straight to the browser.

If the app says port 8000 is busy, that is fine: it automatically uses the next
free port (8001, 8002, …) and the address it prints is the one to use. The usual
cause is an older copy of the app that is still running.

---

## Where your data lives

| What | Where |
| --- | --- |
| The app itself | The folder you extracted; the private environment is in `.venv` inside it |
| Saved conversations | `C:\Users\<your name>\.module_mesh\sessions` (plain JSON files, only on your computer) |
| API keys | Nowhere — they are held in memory for the browser session and are gone when the app stops |

Nothing is uploaded anywhere. There is no account and no telemetry.

---

## Troubleshooting

| What you see | What it means and what to do |
| --- | --- |
| `[ERROR] Python was not found on this computer.` | Python is not installed, or was installed without ticking “Add python.exe to PATH”. Do Step 1 again and tick that box. |
| `[ERROR] This app needs Python 3.10 or newer.` | The Python on your PC is too old, or it is the Microsoft Store placeholder. Install the current Python from <https://www.python.org/downloads/> and tick “Add python.exe to PATH”. |
| Typing `python` opens the Microsoft Store | That is the Store placeholder, not Python. Install Python from python.org (Step 1). It is fine to have both, as long as the real one is installed. |
| `[ERROR] The download/install step failed.` | Usually the internet connection, a company/school proxy, or antivirus. Check you are online, then double-click the launcher again — it retries what is missing. |
| The black window flashes and disappears | A failure path always pauses, so this is rare. Open the folder, hold `Shift` + right-click an empty area, choose **Open PowerShell window here**, type `.\launch-windows.bat`, press `Enter`, and read the last lines. |
| `Windows protected your PC` | Click **More info** → **Run anyway**. |
| Double-clicking does nothing | Right-click `launch-windows.bat` → **Properties** → tick **Unblock** → **OK**, then try again. |
| The browser did not open | Open your browser yourself and type the address the black window printed, e.g. `http://127.0.0.1:8000`. |
| The page says **Session released — Reload** | Your tab sat idle long enough for the server to free its memory. Click Reload; saved sessions are untouched. |
| “Port 8000 is already in use” | The launcher already picks the next free port for you. Nothing to do — use the address the window prints. |
| Antivirus asks about a “script” | It is this launcher: it creates a private Python folder and installs the app's own files. Allow it. |
| Something else | Open an issue on the project's GitHub page and copy the last ~20 lines from the black window into it. |

---

## Removing the app

1. Close the black window (or press `Ctrl`+`C` in it).
2. Delete the folder you extracted.
3. Optional: delete `C:\Users\<your name>\.module_mesh` to remove saved conversations.

Python itself can stay — other programs use it. If you installed it only for this
app, it can be removed from **Settings → Apps**.

---

## Optional: the command-line way

Only if you want it — everything above can be done by clicking. Open
**PowerShell** in the project folder and run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m machinelearningmachine.cli serve --host 127.0.0.1 --port 8000
```

Then open <http://127.0.0.1:8000>. The same three commands work on macOS and
Linux with `python3` in place of `py -3` and `.venv/bin/python` in place of
`.\.venv\Scripts\python.exe`.

---

## For the curious: is my computer safe?

- The app listens on `127.0.0.1` only — that address means “this computer” and is
  not reachable from other machines. It refuses to open itself to the network
  unless an operator deliberately passes extra flags.
- The dashboard loads nothing from the internet: every script and style it uses
  ships inside the folder you extracted.
- The page reader (which can fetch a web address you paste) is switched off
  unless the app is started with `--enable-url-reader`.
- Details, including what the app deliberately does *not* protect against, are in
  [`SECURITY.md`](SECURITY.md).
