# Contributing to BeoSound 5c

Thank you for your interest in contributing to the BeoSound 5c project! This project aims to bring new life to Bang & Olufsen BeoSound 5 devices using modern technology.

## Getting Started

### Development Environment

1. **Without BeoSound 5 hardware** (emulation mode):
   ```bash
   cd web
   python3 -m http.server 8000
   # Open http://localhost:8000 in your browser
   ```
   Use mouse wheel for laser pointer, arrow keys for navigation wheel, and Enter for GO button.

2. **With BeoSound 5 hardware**:
   - SSH to your device: `ssh beosound5c.local`
   - Install services: `cd services/system && sudo ./install-services.sh`
   - See [README.md](README.md) for full setup instructions

### Project Structure

- `web/` - Web interface (HTML, CSS, JavaScript)
- `services/` - Python and Bash services for hardware integration
- `tools/` - Utility scripts for debugging and setup
- `tests/` - Test files and debugging utilities

## How to Contribute

### Reporting Bugs

1. Check existing issues to avoid duplicates
2. Include your setup details (Raspberry Pi model, OS version, etc.)
3. Provide steps to reproduce the issue
4. Include relevant logs from `journalctl -u beo-* -f`

### Suggesting Features

1. Open an issue describing the feature
2. Explain the use case and how it benefits the project
3. If possible, outline a proposed implementation approach

### Submitting Code

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Make your changes
4. Test thoroughly (both emulation mode and on hardware if possible)
5. Commit with clear, descriptive messages
6. Push to your fork and open a Pull Request

### Code Style

- **Python**: Follow PEP 8 guidelines
- **JavaScript**: Use consistent formatting with the existing codebase
- **Shell scripts**: Use `shellcheck` for linting
- Keep changes focused and minimal - avoid unrelated refactoring

### Testing

- Test UI changes in both emulation mode and on real hardware when possible
- For hardware-specific changes, document testing steps clearly
- Include any necessary test files or debugging aids

## Areas Where Help is Needed

- Documentation improvements
- Testing on different Raspberry Pi models
- Home Assistant integration examples
- Sonos and Spotify integration enhancements
- Accessibility improvements
- Multi-language support

## Questions?

Open an issue with your question, and we'll do our best to help.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
