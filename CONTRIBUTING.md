# Contributing to BeoSound 5c

## Reporting Issues

**Installation/configuration problems**: Email markus@beosound5c.com — I'm happy to help troubleshoot your setup.

**Bugs in the system**: Open a GitHub issue with steps to reproduce and relevant logs (`journalctl -u beo-* -f`).

## Suggesting Features

Email markus@beosound5c.com with your idea and use case.

## Submitting Code

This project is built for my personal setup, but contributions should be **as generic as possible**:

- **Setup-specific logic** (e.g., what happens when a button is pressed) belongs in Home Assistant automations, not the codebase
- **User-specific values** belong in configuration files (`/etc/beosound5c/config.env`, `web/js/config.js`)
- **Generic features** that work across different setups are welcome in the project

When adding features:
- Ensure you follow the architecture in the project e.g., with Source vs. Player vs. VolumeAdapter
- Keep changes minimal and focused
- Ensure they work in emulator mode where possible and reasonable — add mocks where needed so others can test without hardware

## License

By contributing, you agree that your contributions will be part of the License for the project
