# Security

Deck runs actions on your Mac when a button on the iPad is tapped, so security problems matter.

## What Deck is designed to do

- Run only the buttons listed in your own `config.json`. The iPad sends a button's name, never a command.
- Answer only devices that have entered your PIN, over an encrypted connection.
- Stay on your local network. It is not meant to be reachable from the internet.
- Refuse on-screen actions while the Mac is locked.

## Reporting a problem

If you find a way around any of the above, please do not open a public issue. Use GitHub's private vulnerability reporting on this repository (Security, Report a vulnerability). Include the macOS and iPad versions and the steps to reproduce it.

## Keeping your own copy safe

- Never share or commit `config.json` (it holds your PIN) or the `certs` folder (it holds your private keys). Both are in `.gitignore`.
- Do not forward Deck's port on your router.
- If you think your PIN is known, change `"pin"` in `config.json` and delete `~/.deck/tokens.json` to sign out every device.
