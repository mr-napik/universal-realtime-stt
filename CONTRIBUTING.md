# Contributing to universal-realtime-stt

Thank you for your interest in improving realtime-stt.

Contributions are entirely optional but very welcome.

## Ways to Contribute

- Reporting bugs
- Adding support for new STT providers
- Performance and feature improvements
- Improving test coverage

## Before Submitting Code

Please:

1. Open an issue first to discuss larger changes.
2. Keep changes focused.
3. Ensure all tests pass (`pytest`).
4. Follow the existing architecture and style.

## Provider Implementations

If adding a new provider:

- Follow the `RealtimeSttProvider` protocol.
- Keep dependencies minimal.
- Maintain queue-based async architecture.

## Philosophy

This project aims to remain:

- Lightweight
- Provider-agnostic
- Dependency-minimal

Please preserve these principles in contributions.

## License

By contributing, you agree that your contributions will be 
licensed under the MIT License.
