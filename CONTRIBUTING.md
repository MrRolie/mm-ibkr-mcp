# Contributing to mm-ibkr-gateway

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

## Getting Started

### Prerequisites

- Python 3.11 or higher
- Poetry for dependency management
- IBKR account with paper trading access (for integration tests)
- IBKR Gateway or TWS

### Development Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/MrRolie/mm-ibkr-gateway.git
   cd mm-ibkr-gateway
   ```

2. **Install dependencies**:
   ```bash
   poetry install
   ```

3. **Configure runtime settings**:
   ```bash
   # Optional: secrets (API_KEY/ADMIN_TOKEN)
   cp .env.example .env

   # Create config.json (operational settings)
   # See deploy/windows/README.md for the full list of keys
   ```

4. **Run tests**:
   ```bash
   # Unit tests only
   poetry run pytest -m "not integration"

   # All tests (requires IBKR Gateway running)
   poetry run pytest
   ```

## Development Workflow

### Branch Strategy

- `main` - Stable, tested code
- `develop` - Integration branch (if used)
- Feature branches: `feature/your-feature-name`
- Bug fixes: `fix/issue-description`

### Making Changes

1. **Create a branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**:
   - Write code following existing style
   - Add tests for new functionality
   - Update documentation

3. **Run quality checks**:
   ```bash
   # Format code
   poetry run black .
   poetry run isort .

   # Lint
   poetry run flake8

   # Type check
   poetry run mypy ibkr_core api mcp_server

   # Run tests
   poetry run pytest -m "not integration"
   ```

4. **Commit**:
   ```bash
   git add .
   git commit -m "feat: add new feature"
   ```

   Use conventional commits:
   - `feat:` - New feature
   - `fix:` - Bug fix
   - `docs:` - Documentation changes
   - `test:` - Test changes
   - `refactor:` - Code refactoring
   - `chore:` - Maintenance tasks

5. **Push and create PR**:
   ```bash
   git push origin feature/your-feature-name
   ```

## Code Style

- **Formatting**: Black with line length 100
- **Import sorting**: isort with black profile
- **Type hints**: Use type hints for all functions
- **Docstrings**: Google style docstrings for public APIs

## Testing Guidelines

### Test Organization

- Unit tests: Mock external dependencies (IBKR client)
- Integration tests: Mark with `@pytest.mark.integration`
- Place tests in `tests/` matching module structure

### Test Requirements

- All new features must have tests
- Aim for >80% code coverage
- Integration tests should be idempotent

### Running Specific Tests

```bash
# Single file
pytest tests/test_client.py

# Single test
pytest tests/test_client.py::test_connection

# By marker
pytest -m integration
```

## Documentation

Update documentation when:
- Adding new features
- Changing public APIs
- Modifying configuration options
- Adding new dependencies

Files to update:
- `README.md` - User-facing documentation
- `api/API.md` - API endpoint documentation
- `.context/PHASE_PLAN.md` - Implementation tracking
- `.context/TODO_BACKLOG.md` - Task tracking
- Docstrings in code

## Pull Request Process

1. **Ensure tests pass**:
   ```bash
   poetry run pytest -m "not integration"
   ```

2. **Update documentation** if needed

3. **Create PR** with:
   - Clear title and description
   - Reference related issues
   - List of changes
   - Testing performed

4. **Code review**: Maintainers will review and provide feedback

5. **Address feedback**: Make requested changes

6. **Merge**: Maintainers will merge when approved

## Questions?

- Open an issue for bugs or feature requests at https://github.com/MrRolie/mm-ibkr-gateway/issues
- Start a discussion for questions at https://github.com/MrRolie/mm-ibkr-gateway/discussions
- For security issues, use GitHub Security Advisories

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
