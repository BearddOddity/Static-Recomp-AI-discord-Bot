# Hosting

How to keep the bot online around the clock without keeping your own PC on, and how it keeps itself up to date.

## What "always on" needs

A Discord bot holds an open connection to Discord all the time, so it has to run on a machine that never sleeps. The AI itself doesn't run there: with OpenRouter, the models run in the cloud and the bot only sends them messages. That means a very small machine is enough: 1 CPU core and 1 GB of memory.

Once installed, the bot looks after itself:

- **Starts at boot** and **restarts after a crash**.
- **Updates itself** from GitHub: every 30 minutes it checks for new commits on its branch, pulls them, and restarts. It never overwrites files you edited by hand.
- **Stays answering** when free models change: if a model disappears or is busy, it switches to another free OpenRouter model.
- **Asks for help** when it can't fix something itself. A model swap becomes a pull request, a dead key becomes an issue, and both are posted in `#mod-log`. See [AI health](#ai-health).

## Option 1: a free cloud server (recommended)

Several cloud providers have free tiers with a small Linux server that's included for free indefinitely, such as Oracle Cloud's *Always Free* virtual machines and Google Cloud's free *e2-micro*. Free tiers and their limits change, so check the provider's current terms before you sign up. They usually ask for a card to verify you, even when you stay on the free tier.

1) Create the smallest free **Ubuntu** server the provider offers and connect to it (the provider's console has an SSH button).

2) Install Git and Python, then download the bot with `git clone`. A clone is needed for self-updates; the ZIP download can't update itself.

    ```sh
    sudo apt-get update && sudo apt-get install -y git python3 python3-venv
    git clone https://github.com/BearddOddity/Static-Recomp-AI-discord-Bot bot
    cd bot
    ```

3) Fill in the settings. `nano` is a simple editor: save with Ctrl+O, exit with Ctrl+X.

    ```sh
    cp .env.example .env
    nano .env
    ```

4) Install it as a service:

    ```sh
    ./deploy/install-linux.sh
    ```

That's it. Close the window; the bot keeps running and comes back after reboots. To watch it: `journalctl -u ai-discord-bot -f`. To stop it: `sudo systemctl stop ai-discord-bot`.

## Option 2: Docker

On any machine with Docker (a server, a NAS, a Raspberry Pi):

```sh
git clone https://github.com/BearddOddity/Static-Recomp-AI-discord-Bot bot
cd bot
cp .env.example .env    # then fill it in
docker compose up -d
```

Docker runs the bot from the cloned folder, so self-updates work. It restarts the bot after each update and starts it when the machine boots (with Docker set to start at boot, which is the default on most systems).

## Option 3: a PC or Raspberry Pi at home

- **Linux or Raspberry Pi:** same as Option 1, from step 2.
- **Windows:** run `start.bat` once to set up, then run `deploy\install-windows.bat`. The bot then starts by itself, minimised, every time you sign in. It's only online while the PC is on.
- **macOS:** run `./start.sh` in a terminal. It restarts the bot after updates and crashes, but you start it after each reboot.

## Why not GitHub Actions or a free web host?

- **GitHub Actions** runs builds and tests. Each job is stopped after at most 6 hours, and Actions isn't meant to run a service that's always on.
- **Free web hosts that sleep when idle** wake up on web traffic, not on Discord messages, so the bot would be offline most of the time.

## AI health

Free models on OpenRouter are added and removed often. The bot handles it like this:

What happened|What the bot does|What you do
-|-|-
A model is busy (rate limit)|Uses the next model on the list, or another free model|Nothing
A model was removed|Switches to a working free model, posts in `#mod-log`, and opens a **pull request** that sets it in your server profile|Merge the PR. The bot updates itself and keeps the new model after restarts
The key stopped working|Posts in `#mod-log` and opens an **issue**. A key is a secret, so it never goes in a pull request|Put a new key in `.env` and restart
No model works at all|Posts in `#mod-log` and opens an issue|Pick a model that's available today

The pull requests and issues need these in `.env`:

```
HEALTH_GITHUB_REPO=BearddOddity/Static-Recomp-AI-discord-Bot
HEALTH_GITHUB_TOKEN=github_pat_...
```

Make the token at <https://github.com/settings/personal-access-tokens/new>: a **fine-grained** token, for **only this repository**, with **Contents**, **Pull requests** and **Issues** set to *Read and write*. The bot only opens pull requests and issues. It never merges anything, so every change is still reviewed by a person.

Pull requests change the file in `HEALTH_PR_FILE`, which defaults to your `SERVER_PROFILE`. Keep model names there rather than in `.env`, because a setting in `.env` overrides the profile, and the merged change would have no effect. Without a server profile, the bot opens an issue that says which model to put in `.env`.

[← Back to the README](../README.md)
